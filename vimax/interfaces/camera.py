from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Union, Dict, Tuple, Any

from vimax.interfaces._int_coerce import coerce_int, coerce_int_list



class Camera(BaseModel):
    idx: int = Field(
        description="The index of the camera in the scene, starting from 0.",
    )

    active_shot_idxs: List[int] = Field(
        description="The indices of the shots that the camera can film.",
    )

    parent_cam_idx: Optional[int] = Field(
        default=None,
        description="The index of the parent camera. If the camera has no parent, set this to None.",
    )

    parent_shot_idx: Optional[int] = Field(
        default=None,
        description="The index of the dependent shot. If the camera has no parent, set this to None.",
    )

    reason: Optional[str] = Field(
        default=None,
        description="The reason for the selection of the parent camera. If the camera has no parent, set this to None.",
    )

    parent_shot_idx: Optional[int] = Field(
        default=None,
        description="The index of the dependent shot. If the camera has no parent, set this to None.",
    )

    is_parent_fully_covers_child: Optional[bool] = Field(
        default=None,
        description="Whether the parent camera fully covers the child camera's content. If the camera has no parent, set this to None.",
    )

    missing_info: Optional[str] = Field(
        default=None,
        description="The missing information in the child shot that is not covered by the parent shot. If the parent shot fully covers the child shot, set this to None.",
    )

    _coerce_int_fields = field_validator(
        "idx", "parent_cam_idx", "parent_shot_idx", mode="before"
    )(coerce_int)
    _coerce_int_list_fields = field_validator(
        "active_shot_idxs", mode="before"
    )(coerce_int_list)
