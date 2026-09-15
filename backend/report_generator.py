"""
report_generator.py — Incident Report PDF Generator for SIH26162
================================================================
Generates a polished, professional single-page incident report PDF using ReportLab.
Designed to match the tactical SOC mission-control aesthetic while preserving a clean,
ink-efficient light background suitable for high-resolution printing and formal audit.

Contents:
  - Header band: dark tactical banner with cyber cyan/crimson accents & severity badge
  - Incident telemetry: structured data grid (timestamp, class, severity, confidence)
  - Visual evidence: annotated snapshot image with frame border & caption
  - Automated protocol response: actionable security dispatch notice
  - Footer: formal disclaimer & confidentiality markings with top divider rule
"""

import os
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    HRFlowable,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


def generate_incident_report(
    event: dict,
    output_pdf_path: str,
    snapshot_image_path: str | None = None,
) -> str | None:
    """
    Generate a professional 1-page PDF incident report for a fire/smoke detection event.

    Parameters
    ----------
    event : dict
        Dict containing at least: timestamp, class, confidence, severity.
    output_pdf_path : str
        Target destination path for the generated PDF.
    snapshot_image_path : str, optional
        Path to an annotated snapshot JPEG/PNG image, if available.

    Returns
    -------
    str | None
        output_pdf_path on success, or None on failure.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(output_pdf_path)), exist_ok=True)

        # 0.5 inch margins (36 pt) ensuring content fits comfortably on a single printable page
        # Printable width: 612 - 72 = 540 pt
        doc = SimpleDocTemplate(
            output_pdf_path,
            pagesize=letter,
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()

        # Extract & sanitize event fields
        timestamp = str(event.get("timestamp", "N/A"))
        detection_class = str(event.get("class", "N/A")).upper()
        severity = str(event.get("severity", "N/A")).upper()

        try:
            conf_val = float(event.get("confidence", 0.0)) * 100
            confidence_str = f"{conf_val:.1f}%"
        except (ValueError, TypeError):
            confidence_str = str(event.get("confidence", "N/A"))

        # Color mapping corresponding to dashboard severity palette
        if severity == "HIGH":
            sev_badge_bg = colors.HexColor("#dc2626")       # Crimson red
            sev_text_color = colors.HexColor("#dc2626")
            sev_accent_bar = colors.HexColor("#ff003c")
            sev_alert_bg = colors.HexColor("#fef2f2")
            sev_alert_border = colors.HexColor("#fca5a5")
        elif severity == "MEDIUM":
            sev_badge_bg = colors.HexColor("#d97706")       # Tactical amber
            sev_text_color = colors.HexColor("#d97706")
            sev_accent_bar = colors.HexColor("#ffb703")
            sev_alert_bg = colors.HexColor("#fffbeb")
            sev_alert_border = colors.HexColor("#fde68a")
        else:
            sev_badge_bg = colors.HexColor("#059669")       # Tactical emerald
            sev_text_color = colors.HexColor("#059669")
            sev_accent_bar = colors.HexColor("#00ff9d")
            sev_alert_bg = colors.HexColor("#f0fdf4")
            sev_alert_border = colors.HexColor("#bbf7d0")

        elements = []

        # -------------------------------------------------------------------
        # 1. Header Band: Tactical SOC dark bar with dual accent borders
        # -------------------------------------------------------------------
        header_left = Paragraph(
            '<font color="#38bdf8" size="7.5"><b>SIH-26162 TACTICAL SURVEILLANCE &amp; THREAT DETECTION CORE</b></font><br/>'
            '<font color="#ffffff" size="15"><b>FIRE DETECTION INCIDENT REPORT</b></font><br/>'
            '<font color="#94a3b8" size="8">AUTOMATED REAL-TIME CRITICAL EVENT REPORT &amp; VISUAL AUDIT LOG</font>',
            styles["Normal"]
        )

        badge_content = Paragraph(
            f'<para align="center">'
            f'<font color="#ffffff" size="8"><b>CRITICAL PROTOCOL</b></font><br/>'
            f'<font color="#ffffff" size="12"><b>SEVERITY: {severity}</b></font>'
            f'</para>',
            styles["Normal"]
        )
        badge_table = Table([[badge_content]], colWidths=[145])
        badge_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), sev_badge_bg),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#ffffff")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))

        header_table = Table([[header_left, badge_table]], colWidths=[385, 155])
        header_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#0f172a")),
            ("LINEABOVE", (0, 0), (-1, -1), 3.5, sev_accent_bar),
            ("LINEBELOW", (0, 0), (-1, -1), 2.0, colors.HexColor("#00f0ff")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 10))

        # -------------------------------------------------------------------
        # 2. Incident Telemetry Grid (Metadata table)
        # -------------------------------------------------------------------
        lbl_style = ParagraphStyle(
            "LblStyle",
            parent=styles["Normal"],
            fontSize=8,
            leading=10,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#475569"),
        )
        val_style = ParagraphStyle(
            "ValStyle",
            parent=styles["Normal"],
            fontSize=9.5,
            leading=12,
            fontName="Helvetica",
            textColor=colors.HexColor("#0f172a"),
        )
        sev_val_style = ParagraphStyle(
            "SevValStyle",
            parent=styles["Normal"],
            fontSize=10,
            leading=12,
            fontName="Helvetica-Bold",
            textColor=sev_text_color,
        )

        telemetry_data = [
            [
                Paragraph("INCIDENT TIMESTAMP:", lbl_style),
                Paragraph(timestamp, val_style),
                Paragraph("TARGET CLASSIFICATION:", lbl_style),
                Paragraph(detection_class, val_style),
            ],
            [
                Paragraph("ALERT SEVERITY:", lbl_style),
                Paragraph(severity, sev_val_style),
                Paragraph("AI CONFIDENCE:", lbl_style),
                Paragraph(confidence_str, val_style),
            ],
        ]

        meta_table = Table(telemetry_data, colWidths=[125, 145, 125, 145])
        meta_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
            ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f1f5f9")),
            ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#ffffff")),
            ("BACKGROUND", (3, 0), (3, -1), colors.HexColor("#ffffff")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        elements.append(meta_table)
        elements.append(Spacer(1, 10))

        # -------------------------------------------------------------------
        # 3. Visual Evidence Section (Annotated Snapshot Image)
        # -------------------------------------------------------------------
        sec_header = Paragraph(
            '<font color="#0f172a" size="10"><b>OPTICAL SURVEILLANCE EVIDENCE</b></font> '
            '<font color="#64748b" size="8">// REAL-TIME BOUNDING BOX CAPTURE</font>',
            styles["Normal"]
        )
        elements.append(sec_header)
        elements.append(Spacer(1, 5))

        if snapshot_image_path and os.path.isfile(snapshot_image_path):
            img_w, img_h = 470, 264  # Standard 16:9 ratio
            try:
                snapshot_img = Image(snapshot_image_path, width=img_w, height=img_h)
                caption = Paragraph(
                    '<font color="#64748b" size="7.5"><i>'
                    'Figure 1.0: Real-time optical capture with YOLOv8 bounding box overlay generated at confirmed threshold trigger.'
                    '</i></font>',
                    styles["Normal"]
                )
                img_table = Table([[snapshot_img], [caption]], colWidths=[540])
                img_table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                    ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e1")),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("TOPPADDING", (0, 0), (0, 0), 6),
                    ("BOTTOMPADDING", (0, 0), (0, 0), 4),
                    ("TOPPADDING", (0, 1), (0, 1), 2),
                    ("BOTTOMPADDING", (0, 1), (0, 1), 6),
                ]))
                elements.append(img_table)
            except Exception as img_err:
                print(f"[report_generator] Error embedding snapshot image: {img_err!r}")
        else:
            placeholder_p = Paragraph(
                '<para align="center"><font color="#64748b" size="8.5">'
                '<b>[ OPTICAL SNAPSHOT RECORDING UNAVAILABLE FOR THIS EVENT ]</b>'
                '</font></para>',
                styles["Normal"]
            )
            placeholder_table = Table([[placeholder_p]], colWidths=[540])
            placeholder_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e1")),
                ("TOPPADDING", (0, 0), (-1, -1), 18),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 18),
            ]))
            elements.append(placeholder_table)

        elements.append(Spacer(1, 10))

        # -------------------------------------------------------------------
        # 4. Operational Protocol Response Box
        # -------------------------------------------------------------------
        protocol_p = Paragraph(
            '<font color="#991b1b" size="8"><b>AUTOMATED PROTOCOL RESPONSE:</b></font> '
            '<font color="#7f1d1d" size="7.5">'
            'High-severity threshold confirmed by dual-persistence detector. Real-time emergency notifications '
            'dispatched via Telegram Bot &amp; SMS gateway. Incident archived to persistent audit database. '
            'Immediate site inspection and emergency evacuation protocols recommended.'
            '</font>',
            styles["Normal"]
        )
        protocol_table = Table([[protocol_p]], colWidths=[540])
        protocol_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), sev_alert_bg),
            ("BOX", (0, 0), (-1, -1), 1, sev_alert_border),
            ("LINEBEFORE", (0, 0), (0, -1), 3.5, sev_badge_bg),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))
        elements.append(protocol_table)
        elements.append(Spacer(1, 10))

        # -------------------------------------------------------------------
        # 5. Document Footer & Official Disclaimer
        # -------------------------------------------------------------------
        elements.append(HRFlowable(
            width="100%",
            thickness=0.75,
            color=colors.HexColor("#cbd5e1"),
            spaceBefore=0,
            spaceAfter=4,
        ))

        footer_left = Paragraph(
            '<font color="#64748b" size="7.5">'
            'Generated by SIH26162 AI Detection System — prototype, not a certified safety system.'
            '</font>',
            styles["Normal"]
        )
        footer_right = Paragraph(
            '<para align="right"><font color="#94a3b8" size="7.5">'
            'CONFIDENTIAL // INCIDENT AUDIT REPORT'
            '</font></para>',
            styles["Normal"]
        )
        footer_table = Table([[footer_left, footer_right]], colWidths=[360, 180])
        footer_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        elements.append(footer_table)

        # Build PDF document
        doc.build(elements)
        return output_pdf_path

    except Exception as exc:
        print(f"[report_generator] PDF generation failed: {exc!r}")
        return None
