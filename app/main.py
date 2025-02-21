from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func, text
import requests
from urllib.parse import urljoin, urlparse
from . import models, database
from datetime import datetime
import re
import plotly.graph_objects as go
import plotly
import json

app = FastAPI(title="Robots.txt Analyzer")
# app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Create tables
models.Base.metadata.create_all(bind=database.engine)


def generate_chart(stats: dict) -> str:
    """Generate a Plotly chart and return it as JSON string."""
    fig = go.Figure(
        data=[
            go.Pie(
                labels=["Allowed URLs", "Disallowed URLs", "Comments"],
                values=[
                    stats["allowed_count"],
                    stats["disallowed_count"],
                    stats["comments_count"],
                ],
                hole=0.4,
                marker=dict(
                    colors=[
                        "rgba(0, 255, 0, 0.8)",
                        "rgba(255, 68, 68, 0.8)",
                        "rgba(68, 170, 255, 0.8)",
                    ],
                    line=dict(color=["#00ff00", "#ff4444", "#44aaff"], width=2),
                ),
                textfont=dict(
                    family="'Courier New', monospace", size=12, color="#00ff00"
                ),
            )
        ]
    )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=True,
        legend=dict(
            font=dict(family="'Courier New', monospace", size=12, color="#00ff00")
        ),
        margin=dict(l=20, r=20, t=20, b=20),
        height=400,
    )

    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


def clean_url(url: str) -> str:
    """Clean and validate the URL."""
    url = url.strip().lower()

    # Remove any protocol if present
    url = re.sub(r"^(https?://)?(www\.)?", "", url)

    # Remove any paths or query parameters
    url = url.split("/")[0]

    if not url:
        raise ValueError("Invalid URL")

    return url


def parse_robots_txt(url: str, content: str):
    """Parse robots.txt content and extract directives."""
    lines = content.split("\n")
    current_user_agent = "*"
    allowed = []
    disallowed = []
    comments = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("#"):
            comments.append(line)
            continue

        if ":" not in line:
            continue

        directive, value = [x.strip() for x in line.split(":", 1)]
        directive = directive.lower()

        if directive == "user-agent":
            current_user_agent = value
        elif directive == "allow":
            allowed.append((current_user_agent, value))
        elif directive == "disallow":
            disallowed.append((current_user_agent, value))

    return {
        "allowed": allowed,
        "disallowed": disallowed,
        "comments": comments,
        "stats": {
            "allowed_count": len(allowed),
            "disallowed_count": len(disallowed),
            "comments_count": len(comments),
        },
    }


@app.get("/")
async def home(request: Request):
    """Render the home page."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/stats")
async def stats(
    request: Request, page: int = 1, db: Session = Depends(database.get_db)
):
    """Show global statistics across all analyzed domains."""
    per_page = 10  # Number of items per page

    # Get total counts
    totals = (
        db.query(models.RobotsAnalysis)
        .with_entities(
            func.sum(models.RobotsAnalysis.allowed_count).label("total_allowed"),
            func.sum(models.RobotsAnalysis.disallowed_count).label("total_disallowed"),
            func.sum(models.RobotsAnalysis.comments_count).label("total_comments"),
            func.count(models.RobotsAnalysis.id).label("total_domains"),
        )
        .first()
    )

    # Get most common allowed directories
    allowed_patterns = (
        db.query(
            models.AllowedUrl.url_pattern,
            func.count(models.AllowedUrl.url_pattern).label("count"),
        )
        .group_by(models.AllowedUrl.url_pattern)
        .order_by(text("count DESC"))
        .limit(10)
        .all()
    )

    # Get most common disallowed directories
    disallowed_patterns = (
        db.query(
            models.DisallowedUrl.url_pattern,
            func.count(models.DisallowedUrl.url_pattern).label("count"),
        )
        .group_by(models.DisallowedUrl.url_pattern)
        .order_by(text("count DESC"))
        .limit(10)
        .all()
    )

    # Get domain type statistics
    domain_stats = []
    # First get all domains with their URLs
    all_domains = db.query(models.RobotsAnalysis.url).all()

    # Extract and count TLDs
    tld_stats = {}
    for domain in all_domains:
        url = domain.url.strip().lower()
        # Remove http:// or https:// if present
        url = url.replace("http://", "").replace("https://", "")
        # Get the last part after the dot
        parts = url.split(".")
        if len(parts) > 1:
            tld = parts[-1]
            if tld not in tld_stats:
                tld_stats[tld] = {"count": 0, "domains": []}
            tld_stats[tld]["count"] += 1
            tld_stats[tld]["domains"].append(url)

    # Now count comments for each TLD
    for tld, data in tld_stats.items():
        comment_count = (
            db.query(func.count(models.RobotsComment.id))
            .join(models.RobotsAnalysis)
            .filter(
                models.RobotsAnalysis.url.in_([f"https://{d}" for d in data["domains"]])
            )
            .scalar()
            or 0
        )

        if comment_count > 0:  # Only include TLDs with comments
            domain_stats.append({"tld": tld, "comment_count": comment_count})

    # Sort by comment count and take top 10
    domain_stats.sort(key=lambda x: x["comment_count"], reverse=True)
    domain_stats = domain_stats[:10]

    print("Domain Stats:", domain_stats)  # Debug output

    # Get most common user agents with categorization (excluding "*")
    user_agents = (
        db.query(
            models.AllowedUrl.user_agent,
            func.count(func.distinct(models.AllowedUrl.id)).label("allowed_count"),
        )
        .filter(models.AllowedUrl.user_agent != "*")
        .group_by(models.AllowedUrl.user_agent)
        .order_by(text("allowed_count DESC"))
        .limit(15)
        .all()
    )

    # Get the disallowed counts for these user agents (excluding "*")
    user_agent_names = [agent.user_agent for agent in user_agents]
    disallowed_counts = {
        row.user_agent: row.disallowed_count
        for row in db.query(
            models.DisallowedUrl.user_agent,
            func.count(func.distinct(models.DisallowedUrl.id)).label(
                "disallowed_count"
            ),
        )
        .filter(
            models.DisallowedUrl.user_agent.in_(user_agent_names),
            models.DisallowedUrl.user_agent != "*",
        )
        .group_by(models.DisallowedUrl.user_agent)
        .all()
    }

    # Categorize user agents and assign colors
    def categorize_user_agent(agent):
        agent = agent.lower()
        if "googlebot" in agent:
            return "Googlebot", "rgba(0, 255, 0, 0.8)"  # Terminal Green
        elif "bingbot" in agent:
            return "Bingbot", "rgba(0, 255, 255, 0.8)"  # Neon Cyan
        elif "yandexbot" in agent:
            return "Yandexbot", "rgba(0, 255, 128, 0.8)"  # Bright Spring Green
        elif "baiduspider" in agent:
            return "Baiduspider", "rgba(128, 255, 0, 0.8)"  # Chartreuse
        elif "facebook" in agent:
            return "Facebook Bot", "rgba(0, 128, 255, 0.8)"  # Bright Blue
        elif "twitter" in agent:
            return "Twitter Bot", "rgba(0, 255, 191, 0.8)"  # Aquamarine
        elif "pinterest" in agent:
            return "Pinterest Bot", "rgba(191, 255, 0, 0.8)"  # Lime
        elif "linkedin" in agent:
            return "LinkedIn Bot", "rgba(0, 191, 255, 0.8)"  # Deep Sky Blue
        elif "slurp" in agent:
            return "Yahoo Bot", "rgba(64, 255, 128, 0.8)"  # Medium Spring Green
        elif "duckduckbot" in agent:
            return "DuckDuckGo Bot", "rgba(128, 255, 64, 0.8)"  # Light Green
        elif "yeti" in agent:
            return "Naver Bot", "rgba(0, 255, 160, 0.8)"  # Sea Green
        elif "teoma" in agent:
            return "Ask Bot", "rgba(160, 255, 0, 0.8)"  # Yellow Green
        elif "seznambot" in agent:
            return "Seznam Bot", "rgba(0, 255, 96, 0.8)"  # Spring Green
        elif "gptbot" in agent:
            return "GPT Bot", "rgba(96, 255, 0, 0.8)"  # Bright Lime
        elif "applebot" in agent:
            return "Apple Bot", "rgba(0, 255, 224, 0.8)"  # Light Cyan
        else:
            # Use the agent name itself as the category
            name = agent.split("/")[0].split("-")[0].title()
            return name, "rgba(32, 255, 192, 0.8)"  # Pale Green

    # Combine the results with categories and colors
    user_agents = [
        {
            "user_agent": agent.user_agent,
            "allowed_count": agent.allowed_count,
            "disallowed_count": disallowed_counts.get(agent.user_agent, 0),
            "category": categorize_user_agent(agent.user_agent)[0],
            "color": categorize_user_agent(agent.user_agent)[1],
        }
        for agent in user_agents
    ]

    # Get comments statistics
    comments_stats = (
        db.query(
            models.RobotsComment.comment,
            func.count(models.RobotsComment.comment).label("frequency"),
            func.length(models.RobotsComment.comment).label("length"),
        )
        .group_by(models.RobotsComment.comment)
        .having(
            func.count(models.RobotsComment.comment) > 1  # Only show repeated comments
        )
        .order_by(text("frequency DESC, length DESC"))
        .limit(10)
        .all()
    )

    # Get latest analyzed domains with pagination
    total_domains = db.query(models.RobotsAnalysis).count()
    total_pages = (total_domains + per_page - 1) // per_page

    latest_domains = (
        db.query(models.RobotsAnalysis)
        .order_by(models.RobotsAnalysis.timestamp.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    # Calculate average counts per domain
    avg_counts = db.query(
        func.avg(models.RobotsAnalysis.allowed_count).label("avg_allowed"),
        func.avg(models.RobotsAnalysis.disallowed_count).label("avg_disallowed"),
        func.avg(models.RobotsAnalysis.comments_count).label("avg_comments"),
    ).first()

    return templates.TemplateResponse(
        "stats.html",
        {
            "request": request,
            "totals": totals,
            "allowed_patterns": allowed_patterns,
            "disallowed_patterns": disallowed_patterns,
            "comments_stats": comments_stats,
            "user_agents": user_agents,
            "latest_domains": latest_domains,
            "current_page": page,
            "total_pages": total_pages,
            "avg_counts": avg_counts,
            "domain_stats": domain_stats,
        },
    )


@app.post("/analyze")
async def analyze(request: Request, db: Session = Depends(database.get_db)):
    """Analyze a website's robots.txt file."""
    form = await request.form()
    url = form.get("url", "").strip()

    if not url:
        return templates.TemplateResponse(
            "analysis.html", {"request": request, "error": "URL is required"}
        )

    try:
        clean_domain = clean_url(url)
        full_url = f"https://{clean_domain}"
        robots_url = f"{full_url}/robots.txt"

        response = requests.get(
            robots_url, timeout=20, headers={"User-Agent": "RobotsAnalyzer/1.0"}
        )
        response.raise_for_status()
        content = response.text

    except ValueError as e:
        return templates.TemplateResponse(
            "analysis.html", {"request": request, "error": str(e)}
        )
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        if "SSLError" in error_msg:
            # Try HTTP if HTTPS fails
            try:
                robots_url = f"http://{clean_domain}/robots.txt"
                response = requests.get(
                    robots_url, timeout=10, headers={"User-Agent": "RobotsAnalyzer/1.0"}
                )
                response.raise_for_status()
                content = response.text
                full_url = f"http://{clean_domain}"
            except requests.exceptions.RequestException as e:
                return templates.TemplateResponse(
                    "analysis.html",
                    {
                        "request": request,
                        "error": f"Could not fetch robots.txt: {str(e)}",
                    },
                )
        else:
            return templates.TemplateResponse(
                "analysis.html",
                {
                    "request": request,
                    "error": f"Could not fetch robots.txt: {error_msg}",
                },
            )

    analysis_data = parse_robots_txt(full_url, content)

    # Create database entry
    analysis = models.RobotsAnalysis(
        url=full_url,
        allowed_count=analysis_data["stats"]["allowed_count"],
        disallowed_count=analysis_data["stats"]["disallowed_count"],
        comments_count=analysis_data["stats"]["comments_count"],
    )
    db.add(analysis)
    db.flush()

    # Add allowed URLs
    for user_agent, pattern in analysis_data["allowed"]:
        allowed = models.AllowedUrl(
            url_pattern=pattern, user_agent=user_agent, analysis_id=analysis.id
        )
        db.add(allowed)

    # Add disallowed URLs
    for user_agent, pattern in analysis_data["disallowed"]:
        disallowed = models.DisallowedUrl(
            url_pattern=pattern, user_agent=user_agent, analysis_id=analysis.id
        )
        db.add(disallowed)

    # Add comments
    for comment in analysis_data["comments"]:
        comment_obj = models.RobotsComment(comment=comment, analysis_id=analysis.id)
        db.add(comment_obj)

    db.commit()

    return templates.TemplateResponse(
        "analysis.html",
        {
            "request": request,
            "url": full_url,
            "analysis": analysis,
            "allowed": analysis_data["allowed"],
            "disallowed": analysis_data["disallowed"],
            "comments": analysis_data["comments"],
            "stats": analysis_data["stats"],
            "timestamp": datetime.utcnow(),
        },
    )
