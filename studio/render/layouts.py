"""Where things go on the frame, per layout: "h" (1920x1080) for the song and lesson videos, "v" (1080x1920) for
the hook cut, the short vertical clip students share."""

from __future__ import annotations

from dataclasses import dataclass

from studio import media

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1 in frame pixels


@dataclass(frozen=True)
class Layout:
    name: str
    width: int
    height: int
    band_h: int  # the lyric band along the bottom
    band_font: float
    label_font: float
    caption_font: float
    margin: float  # labels and pills keep this far from the frame's edges
    brand_font: float  # the brand's text, in the bottom right corner of the title band where there is one, else of the picture
    title_h: int = 0  # a title band along the top, with the topic in it; 0 for none
    title_font: float = 0.0
    band_lines: int = 1  # lines the lyric band wraps a sung line onto
    key_h: int = 0  # a band just above the lyric band for the scene's key line, in a video that has them
    key_font: float = 0.0

    @property
    def view(self) -> Rect:
        """Where the diagram goes: everything between the title band and the lyric band."""
        return (0.0, float(self.title_h), float(self.width), float(self.height - self.band_h))


H = Layout(name="h", width=media.VIDEO_WIDTH, height=media.VIDEO_HEIGHT, band_h=110, band_font=46,
           label_font=30, caption_font=34, margin=20, brand_font=20, key_h=96, key_font=40)

# A phone held upright: the topic on top and the brand in that band's corner, the diagram across the middle, then the
# scene's key line (in a video that has them) right above the line sung, on two lines.
V = Layout(name="v", width=media.VIDEO_HEIGHT, height=media.VIDEO_WIDTH, band_h=300, band_font=58,
           label_font=30, caption_font=34, margin=24, brand_font=22, title_h=260, title_font=62, band_lines=2,
           key_h=170, key_font=50)

LAYOUTS = {"h": H, "v": V}
