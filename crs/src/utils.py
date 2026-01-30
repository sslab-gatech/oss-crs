from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape


def render_template(template_path: Path, context: dict) -> str:
    """Render a Jinja2 template with the given context.

    Args:
        template_path (str | Path): Path to the Jinja2 template file.
        context (dict): Context variables for rendering the template.

    Returns:
        bytes: Rendered template content as bytes.
    """
    template_dir = Path(template_path).parent
    template_file = Path(template_path).name

    env = Environment(
        loader=FileSystemLoader(searchpath=str(template_dir)),
        autoescape=select_autoescape(),
    )
    template = env.get_template(template_file)
    rendered_content = template.render(context)
    return rendered_content
