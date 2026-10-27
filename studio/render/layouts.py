"""Where things go on the frame, per layout. Only "h" (1920x1080) for now; "v" is a stretch goal."""

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
    frq_card: Rect  # the exam question's card
    frq_view: Rect  # where the diagram sits beside the card

    @property
    def view(self) -> Rect:
        """Where the diagram goes: everything above the lyric band."""
        return (0.0, 0.0, float(self.width), float(self.height - self.band_h))


H = Layout(name="h", width=media.VIDEO_WIDTH, height=media.VIDEO_HEIGHT, band_h=110, band_font=46,
           label_font=30, caption_font=34, margin=20,
           frq_card=(64, 48, 800, 1032), frq_view=(830, 40, 1900, 1040))

LAYOUTS = {"h": H}
