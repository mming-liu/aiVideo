from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Union, Dict, Tuple, Literal

from vimax.interfaces._int_coerce import coerce_int, coerce_int_list


class Frame(BaseModel):
    shot_idx: int = Field(
        description="The index of the shot in the sequence, starting from 0."
    )

    frame_type: Literal["first", "last"] = Field(
        description="The type of the frame, 'first' for the first frame of the shot, 'last' for the last frame of the shot."
    )

    cam_idx: int = Field(
        description="The index of the camera used for this frame, starting from 0."
    )

    vis_char_idxs: List[int] = Field(
        description="A list of indices of characters that are visible in this frame, corresponding to the character list provided in the input."
    )

    _coerce_int_fields = field_validator("shot_idx", "cam_idx", mode="before")(coerce_int)
    _coerce_int_list_fields = field_validator("vis_char_idxs", mode="before")(coerce_int_list)
