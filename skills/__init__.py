# Skills package — re-exports for backwards compatibility in app.py
from .triggers import (
    IMG_TRIGGERS,
    VIDEO_TRIGGERS,
    IMAGE_EDIT_TRIGGERS,
    PROMPT_OPTIMIZE_TRIGGERS,
    PROMPT_FRAMEWORKS,
)
from .comfyui import (
    extract_img_prompt as _extract_img_prompt,
    extract_video_prompt as _extract_video_prompt,
    prepare_video_prompt as _prepare_video_prompt,
    optimize_prompt_for_image as _optimize_prompt_for_image,
    upload_image_to_comfyui as _upload_image_to_comfyui,
    build_firered_edit_workflow,
    build_wan_video_workflow as _build_wan_video_workflow,
    build_z_image_turbo_workflow,
    run_comfyui_sync as _run_comfyui_sync,
    run_comfyui_video as _run_comfyui_video,
    run_comfyui_edit as _run_comfyui_edit,
    make_thumbnail as _make_thumbnail,
    WAN_VIDEO_NEGATIVE,
)
from .url_fetch import is_safe_url as _is_safe_url, fetch_url_text
from .telegram_skill import run_telegram as _run_telegram
from .gmail_skill import run_gmail as _run_gmail
from .prompt_optimize import optimize_prompt as _optimize_prompt
