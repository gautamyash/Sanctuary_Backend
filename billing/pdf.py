"""
Professional PDF invoice generation using ReportLab (Feature 6).

Includes a logo wordmark, invoice number, QR code, patient/doctor, itemised
services, taxes, discount, total, and a payment summary.
"""

from io import BytesIO

from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

PRIMARY = colors.HexColor("#003d9b")
MUTED = colors.HexColor("#64748b")


def _qr_drawing(text: str, size: float = 32 * mm) -> Drawing:
    widget = qr.QrCodeWidget(text)
    bounds = widget.getBounds()
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]
    d = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    d.add(widget)
    return d


def render_invoice_pdf(invoice) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=invoice.invoice_number,
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle(
        "h1", parent=styles["Title"], textColor=PRIMARY, fontSize=22, spaceAfter=2
    )
    small = ParagraphStyle("small", parent=styles["Normal"], textColor=MUTED, fontSize=9)
    normal = styles["Normal"]
    right = ParagraphStyle("right", parent=styles["Normal"], alignment=2)

    story = []

    # Header: brand + QR
    header = Table(
        [
            [
                Paragraph("Sanctuary Health", h1),
                _qr_drawing(f"{invoice.invoice_number}|{invoice.total}"),
            ],
            [
                Paragraph("Revenue &amp; Billing", small),
                "",
            ],
        ],
        colWidths=[None, 36 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("SPAN", (1, 0), (1, 1)),
                ("ALIGN", (1, 0), (1, 1), "RIGHT"),
            ]
        )
    )
    story.append(header)
    story.append(Spacer(1, 8 * mm))

    # Invoice meta
    patient_name = getattr(invoice.patient, "name", None) or invoice.patient.email
    doctor_name = invoice.doctor.name if invoice.doctor else "-"
    meta = Table(
        [
            [
                Paragraph(f"<b>Invoice</b><br/>{invoice.invoice_number}", normal),
                Paragraph(
                    f"<b>Status</b><br/>{invoice.get_status_display()} "
                    f"/ {invoice.get_payment_status_display()}",
                    normal,
                ),
                Paragraph(
                    f"<b>Issued</b><br/>{invoice.issued_at.strftime('%d %b %Y')}",
                    normal,
                ),
            ],
            [
                Paragraph(f"<b>Patient</b><br/>{patient_name}", normal),
                Paragraph(f"<b>Doctor</b><br/>{doctor_name}", normal),
                "",
            ],
        ],
        colWidths=[None, None, None],
    )
    meta.setStyle(TableStyle([("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story.append(meta)
    story.append(Spacer(1, 6 * mm))

    # Items
    rows = [["Service", "Qty", "Unit", "Disc", "Tax", "Total"]]
    for item in invoice.items.all():
        rows.append(
            [
                Paragraph(item.description, normal),
                str(item.quantity),
                f"{item.unit_price}",
                f"{item.discount}",
                f"{item.tax}",
                f"{item.total}",
            ]
        )
    if len(rows) == 1:
        rows.append([Paragraph("No services", small), "", "", "", "", ""])
    table = Table(rows, colWidths=[None, 12 * mm, 22 * mm, 20 * mm, 20 * mm, 24 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, PRIMARY),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 6 * mm))

    # Totals
    totals = Table(
        [
            ["Subtotal", f"{invoice.subtotal}"],
            ["Discount", f"-{invoice.discount}"],
            ["Tax", f"{invoice.tax}"],
            ["Total", f"{invoice.total}"],
            ["Amount paid", f"{invoice.amount_paid}"],
            ["Balance due", f"{invoice.balance}"],
        ],
        colWidths=[None, 30 * mm],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("LINEABOVE", (0, 3), (-1, 3), 0.5, MUTED),
                ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold"),
                ("FONTNAME", (0, 5), (-1, 5), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 5), (-1, 5), PRIMARY),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(totals)
    story.append(Spacer(1, 8 * mm))

    # Payment summary
    pays = invoice.payments.all()
    if pays:
        prows = [["Payment", "Method", "Amount", "Date"]]
        for p in pays:
            prows.append(
                [
                    p.get_status_display(),
                    p.get_method_display(),
                    f"{p.amount}",
                    p.paid_at.strftime("%d %b %Y"),
                ]
            )
        ptable = Table(prows, colWidths=[None, 30 * mm, 24 * mm, 28 * mm])
        ptable.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.4, MUTED),
                    ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ]
            )
        )
        story.append(Paragraph("Payment summary", small))
        story.append(Spacer(1, 2 * mm))
        story.append(ptable)

    story.append(Spacer(1, 10 * mm))
    story.append(
        Paragraph(
            "Thank you for choosing Sanctuary Health. This is a "
            "computer-generated invoice.",
            small,
        )
    )

    doc.build(story)
    return buf.getvalue()
