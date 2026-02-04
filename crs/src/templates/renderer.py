from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..crs import CRS
    from ..config.crs_compose import CRSComposeEnv
    from ..target import Target
    from ..config.crs import BuildConfig
    from ..utils import TmpDockerCompose

CUR_DIR = Path(__file__).parent
LIBCRS_PATH = (CUR_DIR / "../../../libCRS").resolve()


def render_template(template_path: Path, context: dict) -> str:
    """Render a Jinja1 template with the given context.

    Args:
        template_path (str | Path): Path to the Jinja1 template file.
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


def render_build_target_docker_compose(
    crs: "CRS",
    target: "Target",
    target_base_image: str,
    build_config: "BuildConfig",
    build_out_dir: Path,
) -> str:
    """Render the docker-compose file for building a target.

    Args:
        crs (CRS): CRS instance.
        target (Target): Target instance.
        build_config (BuildConfig): Build configuration.
        build_out_dir (Path): Output directory for the build.

    Returns:
        str: Rendered docker-compose content as a string.
    """
    template_path = CUR_DIR / "build-target.docker-compose.yaml.j2"
    target_env = target.get_target_env()
    target_env["image"] = target_base_image
    context = {
        "crs": {
            "name": crs.name,
            "path": str(crs.crs_path),
            "builder_dockerfile": str(crs.crs_path / build_config.dockerfile),
            "version": crs.config.version,
        },
        "additional_env": build_config.additional_env,
        "target": target_env,
        "build_out_dir": str(build_out_dir),
        "crs_compose_env": crs.crs_compose_env.get_env(),
        "libCRS_path": str(LIBCRS_PATH),
    }
    return render_template(template_path, context)


def render_run_crs_compose_docker_compose(
    tmp_docker_compose: "TmpDockerCompose",
    crs_compose_name: str,
    crs_compose_env: "CRSComposeEnv",
    crs_list: list["CRS"],
    target: "Target",
) -> str:
    template_path = CUR_DIR / "run-crs-compose.docker-compose.yaml.j2"
    context = {
        "libCRS_path": str(LIBCRS_PATH),
        "crs_compose_name": crs_compose_name,
        "crs_list": crs_list,
        "crs_compose_env": crs_compose_env.get_env(),
        "target_env": target.get_target_env(),
        "target": target,
    }
    return render_template(template_path, context)
