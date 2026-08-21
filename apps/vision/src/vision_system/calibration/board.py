"""ChArUco board specifications, printable PDF generation, and board rendering."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
from reportlab.lib.pagesizes import A3, A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from ..core.aruco import aruco_dictionary
from ..core.config import save_json

INCH_M = 0.0254
BOARD_IMAGE_BORDER_BITS = 1
PDF_MARGIN_MM = 15.0
PDF_TITLE_ROW_MM = 10.0
PDF_DETAIL_ROW_MM = 15.0
PDF_BOARD_VERTICAL_OFFSET_MM = 6.0
PDF_TITLE_FONT_PT = 9
PDF_DETAIL_FONT_PT = 7
PDF_LINE_WIDTH_PT = 0.5
PDF_CONTROL_BAR_Y_MM = 8.0
PDF_CONTROL_BAR_LENGTH_MM = 100.0
PDF_CONTROL_BAR_TICK_MM = 1.5
PDF_CONTROL_BAR_LABEL_X_MM = 48.0
PDF_CONTROL_BAR_LABEL_Y_MM = 10.0
MM_PER_M = 1000.0


@dataclass(frozen=True)
class BoardSpec:
    page_format: str = "a4"
    squares_x: int = 6
    squares_y: int = 8
    square_length_m: float = 0.030
    marker_length_m: float = 0.022
    dictionary: str = "DICT_5X5_100"
    dpi: int = 600

    @property
    def width_m(self) -> float:
        return self.squares_x * self.square_length_m

    @property
    def height_m(self) -> float:
        return self.squares_y * self.square_length_m

    @classmethod
    def for_format(cls, page_format: str) -> BoardSpec:
        normalized = page_format.lower()
        if normalized == "a4":
            return cls()
        if normalized == "a3":
            return cls(
                page_format="a3",
                squares_x=7,
                squares_y=9,
                square_length_m=0.040,
                marker_length_m=0.030,
            )
        raise ValueError(f"unsupported board format: {page_format}")


def create_charuco_board(spec: BoardSpec):
    return cv2.aruco.CharucoBoard(
        (spec.squares_x, spec.squares_y),
        spec.square_length_m,
        spec.marker_length_m,
        aruco_dictionary(spec.dictionary),
    )


def create_charuco_detector(board):
    parameters = cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_NONE
    return cv2.aruco.CharucoDetector(board, cv2.aruco.CharucoParameters(), parameters)


def board_checksum(spec: BoardSpec) -> str:
    payload = json.dumps(spec.__dict__, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def _draw_print_sheet(
    pdf,
    spec: BoardSpec,
    png_path: Path,
    page_width: float,
    page_height: float,
) -> None:
    board_width = spec.width_m * MM_PER_M * mm
    board_height = spec.height_m * MM_PER_M * mm
    left = (page_width - board_width) / 2
    bottom = (page_height - board_height) / 2 + PDF_BOARD_VERTICAL_OFFSET_MM * mm
    pdf.drawImage(str(png_path), left, bottom, board_width, board_height, mask="auto")
    pdf.setFont("Helvetica-Bold", PDF_TITLE_FONT_PT)
    title = (
        f"ChArUco {spec.squares_x}x{spec.squares_y} {spec.page_format.upper()} "
        "- STAMPA AL 100% - NON ADATTARE"
    )
    margin = PDF_MARGIN_MM * mm
    pdf.drawString(margin, page_height - PDF_TITLE_ROW_MM * mm, title)
    pdf.drawRightString(page_width - margin, page_height - PDF_TITLE_ROW_MM * mm, "TOP ^")
    pdf.setFont("Helvetica", PDF_DETAIL_FONT_PT)
    square_mm = spec.square_length_m * MM_PER_M
    marker_mm = spec.marker_length_m * MM_PER_M
    pdf.drawString(
        margin,
        page_height - PDF_DETAIL_ROW_MM * mm,
        f"Quadrato {square_mm:g} mm; marker {marker_mm:g} mm; {spec.dictionary}",
    )
    pdf.setLineWidth(PDF_LINE_WIDTH_PT)
    bar_start = PDF_MARGIN_MM * mm
    bar_end = bar_start + PDF_CONTROL_BAR_LENGTH_MM * mm
    bar_y = PDF_CONTROL_BAR_Y_MM * mm
    tick = PDF_CONTROL_BAR_TICK_MM * mm
    pdf.line(bar_start, bar_y, bar_end, bar_y)
    pdf.line(bar_start, bar_y - tick, bar_start, bar_y + tick)
    pdf.line(bar_end, bar_y - tick, bar_end, bar_y + tick)
    pdf.drawString(
        PDF_CONTROL_BAR_LABEL_X_MM * mm,
        PDF_CONTROL_BAR_LABEL_Y_MM * mm,
        f"barra di controllo: {PDF_CONTROL_BAR_LENGTH_MM:.0f} mm",
    )


def generate_board(output_dir: Path, spec: BoardSpec | None = None) -> tuple[Path, Path, Path]:
    """Render the printable board (PDF), its PNG source and a metadata sidecar."""
    spec = spec or BoardSpec()
    output_dir.mkdir(parents=True, exist_ok=True)
    board = create_charuco_board(spec)
    width_px = round(spec.width_m / INCH_M * spec.dpi)
    height_px = round(spec.height_m / INCH_M * spec.dpi)
    image = board.generateImage(
        (width_px, height_px), marginSize=0, borderBits=BOARD_IMAGE_BORDER_BITS
    )
    prefix = f"charuco-{spec.page_format}"
    png_path = output_dir / f"{prefix}-board.png"
    pdf_path = output_dir / f"{prefix}-print.pdf"
    metadata_path = output_dir / f"{prefix}-board.json"
    if not cv2.imwrite(str(png_path), image):
        raise OSError(f"cannot write {png_path}")
    checksum = hashlib.sha256(image.tobytes()).hexdigest()

    page_size = A3 if spec.page_format == "a3" else A4
    page_width, page_height = page_size
    pdf = canvas.Canvas(str(pdf_path), pagesize=page_size)
    _draw_print_sheet(pdf, spec, png_path, page_width, page_height)
    pdf.save()

    save_json(
        metadata_path,
        {
            **spec.__dict__,
            "width_m": spec.width_m,
            "height_m": spec.height_m,
            "sha256": checksum,
            "print_scale": "100%",
        },
    )
    return pdf_path, png_path, metadata_path
