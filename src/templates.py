"""Email template system for UniMail.

Uses Jinja2 to render email templates from ~/.unimail/templates/.
Provides built-in templates and supports user-defined custom templates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape, TemplateNotFound

from .config import get_config_dir
from .log import get_logger

logger = get_logger(__name__)

TEMPLATES_DIR = get_config_dir() / "templates"

# Built-in templates
BUILTIN_TEMPLATES = {
    "welcome.html": """\
<!DOCTYPE html>
<html>
<head><style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }
h1 { color: #2563eb; }
.footer { margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb; font-size: 0.85em; color: #6b7280; }
</style></head>
<body>
<h1>Welcome, {{ name }}!</h1>
<p>{{ message | default("Thank you for joining us. We're excited to have you on board!") }}</p>
{% if action_url %}
<p><a href="{{ action_url }}" style="background: #2563eb; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">{{ action_text | default("Get Started") }}</a></p>
{% endif %}
<div class="footer">
<p>{{ footer | default("Sent via UniMail") }}</p>
</div>
</body>
</html>
""",
    "notification.html": """\
<!DOCTYPE html>
<html>
<head><style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }
.notification { background: #f3f4f6; border-left: 4px solid #2563eb; padding: 15px; margin: 15px 0; border-radius: 4px; }
.footer { margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb; font-size: 0.85em; color: #6b7280; }
</style></head>
<body>
<h2>{{ title | default("Notification") }}</h2>
<div class="notification">
<p>{{ message }}</p>
{% if details %}
<ul>
{% for item in details %}
<li>{{ item }}</li>
{% endfor %}
</ul>
{% endif %}
</div>
{% if action_url %}
<p><a href="{{ action_url }}">{{ action_text | default("View Details") }}</a></p>
{% endif %}
<div class="footer">
<p>{{ footer | default("Sent via UniMail") }}</p>
</div>
</body>
</html>
""",
    "reply.html": """\
<!DOCTYPE html>
<html>
<head><style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }
.reply-body { margin-bottom: 20px; }
blockquote { border-left: 3px solid #d1d5db; margin: 15px 0; padding: 10px 15px; color: #6b7280; background: #f9fafb; }
.footer { margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb; font-size: 0.85em; color: #6b7280; }
</style></head>
<body>
<div class="reply-body">
{{ body }}
</div>
{% if original_message %}
<blockquote>
<p><strong>On {{ original_date }}, {{ original_from }} wrote:</strong></p>
{{ original_message }}
</blockquote>
{% endif %}
<div class="footer">
<p>{{ signature | default("") }}</p>
</div>
</body>
</html>
""",
}


class TemplateEngine:
    """Jinja2-based email template engine.

    Loads templates from ~/.unimail/templates/ with built-in fallbacks.
    """

    def __init__(self):
        self._ensure_templates_dir()
        self._env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def _ensure_templates_dir(self) -> None:
        """Create templates directory and install built-in templates if missing."""
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        for name, content in BUILTIN_TEMPLATES.items():
            template_path = TEMPLATES_DIR / name
            if not template_path.exists():
                template_path.write_text(content)
                logger.debug(f"Installed built-in template: {name}")

    def render(self, template_name: str, **context: Any) -> str:
        """Render a template with the given context.

        Args:
            template_name: Name of the template file (e.g., 'welcome.html')
            **context: Template variables

        Returns:
            Rendered HTML string

        Raises:
            ValueError: If template not found
        """
        try:
            template = self._env.get_template(template_name)
            rendered = template.render(**context)
            logger.debug(f"Rendered template: {template_name}")
            return rendered
        except TemplateNotFound:
            raise ValueError(
                f"Template not found: {template_name}. "
                f"Available templates: {', '.join(self.list_templates())}"
            )

    def list_templates(self) -> list[str]:
        """List all available template names."""
        templates = []
        if TEMPLATES_DIR.exists():
            for f in TEMPLATES_DIR.iterdir():
                if f.is_file() and f.suffix in (".html", ".txt", ".md"):
                    templates.append(f.name)
        return sorted(templates)

    def template_exists(self, template_name: str) -> bool:
        """Check if a template exists."""
        return (TEMPLATES_DIR / template_name).exists()

    def get_template_content(self, template_name: str) -> Optional[str]:
        """Get the raw content of a template (for preview/editing)."""
        path = TEMPLATES_DIR / template_name
        if path.exists():
            return path.read_text()
        return None


# Singleton
_template_engine: Optional[TemplateEngine] = None


def get_template_engine() -> TemplateEngine:
    """Get the singleton TemplateEngine instance."""
    global _template_engine
    if _template_engine is None:
        _template_engine = TemplateEngine()
    return _template_engine
