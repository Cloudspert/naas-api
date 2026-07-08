"""Render Kubernetes manifests from Jinja2 templates.

Keeping manifests as templates (in app/templates/) means their shape can be
changed without touching the Python code.
"""

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.core.config import get_settings

_env = Environment(
    loader=FileSystemLoader(get_settings().template_dir),
    undefined=StrictUndefined,  # fail loudly on a missing variable
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_manifest(template_name: str, **context) -> dict:
    text = _env.get_template(template_name).render(**context)
    return yaml.safe_load(text)
