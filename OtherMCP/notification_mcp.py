import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_executive_briefing(summary_data: dict) -> bool:
    """Formats and emails today's Career OS standup report, artifacts, and outreach drafts."""
    sender_email = os.getenv("SENDER_EMAIL", "gargshubh200@gmail.com")
    sender_password = os.getenv("SENDER_APP_PASSWORD")
    recipient_email = os.getenv("RECIPIENT_EMAIL", "gargshubh200@gmail.com")

    if not sender_password:
        print("⚠️ SENDER_APP_PASSWORD not set. Skipping email dispatch.")
        return False

    job_records = summary_data.get("job_applications", [])
    hiring_leads = summary_data.get("hiring_leads", [])

    high_priority = [r for r in job_records if r.get("strategy") == "HIGH"]
    medium_priority = [r for r in job_records if r.get("strategy") == "MEDIUM"]

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #1a73e8;">🚀 Career OS: Daily Executive Standup Report</h2>
        <p>Automation completed successfully. Summary of processed leads for today:</p>

        <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
            <tr style="background-color: #f2f2f2;">
                <th style="padding: 10px; border: 1px solid #ddd;">Job Applications Processed</th>
                <th style="padding: 10px; border: 1px solid #ddd; color: #2e7d32;">HIGH Priority</th>
                <th style="padding: 10px; border: 1px solid #ddd; color: #f57c00;">MEDIUM Priority</th>
                <th style="padding: 10px; border: 1px solid #ddd; color: #1a73e8;">Hiring Manager DMs Drafted</th>
            </tr>
            <tr>
                <td style="padding: 10px; border: 1px solid #ddd; text-align: center;">{len(job_records)}</td>
                <td style="padding: 10px; border: 1px solid #ddd; text-align: center; font-weight: bold;">{len(high_priority)}</td>
                <td style="padding: 10px; border: 1px solid #ddd; text-align: center; font-weight: bold;">{len(medium_priority)}</td>
                <td style="padding: 10px; border: 1px solid #ddd; text-align: center; font-weight: bold;">{len(hiring_leads)}</td>
            </tr>
        </table>
    """

    # --- SECTION 1: HIGH PRIORITY APPLICATION ARTIFACTS ---
    if high_priority:
        html_content += """<h3 style="color: #2e7d32;">🔥 High-Priority Application Artifacts</h3>"""
        for item in high_priority:
            html_content += f"""
            <div style="background: #f9f9f9; padding: 15px; border-left: 4px solid #2e7d32; margin-bottom: 15px;">
                <h4 style="margin: 0 0 5px 0;">{item['title']} @ <strong>{item['company']}</strong> ({item['location']})</h4>
                <p style="margin: 0 0 10px 0;"><strong>Match Score:</strong> {item['match_score']}% | <strong>Priority:</strong> {item['strategy']}</p>
                <p style="margin: 0 0 5px 0;"><strong>Generated Documents:</strong></p>
                <ul>
                    <li><a href="{item.get('drive_resume_link', '#')}" target="_blank">Tailored Resume (Google Drive)</a></li>
                </ul>
            </div>
            """

    # --- SECTION 2: STANDALONE HIRING MANAGER OUTREACH DMS ---
    if hiring_leads:
        html_content += """<h3 style="color: #1a73e8;">📩 Today's Hiring Manager Outreach DMs</h3>"""
        for lead in hiring_leads:
            html_content += f"""
            <div style="background: #f0f7ff; padding: 15px; border-left: 4px solid #1a73e8; margin-bottom: 15px;">
                <h4 style="margin: 0 0 5px 0;">{lead['manager_name']} — {lead['manager_title']} (<strong>{lead['company']}</strong>)</h4>
                <p style="margin: 0 0 5px 0;"><strong>LinkedIn Post:</strong> <a href="{lead.get('post_url', '#')}" target="_blank">View Original Post</a></p>

                <div style="background: #ffffff; padding: 12px; border: 1px solid #d0e2ff; margin-top: 10px; border-radius: 4px;">
                    <strong>Drafted DM:</strong>
                    <p style="font-style: italic; white-space: pre-wrap; margin-top: 5px; color: #222;">{lead.get('drafted_dm', 'N/A')}</p>
                </div>
            </div>
            """

    # --- SECTION 3: MEDIUM PRIORITY APPLICATION ARTIFACTS ---
    if medium_priority:
        html_content += """<h3 style="color: #f57c00;">⚡ Medium-Priority Application Artifacts</h3>"""
        for item in medium_priority:
            html_content += f"""
            <div style="background: #fffbe6; padding: 15px; border-left: 4px solid #f57c00; margin-bottom: 15px;">
                <h4 style="margin: 0 0 5px 0;">{item['title']} @ <strong>{item['company']}</strong> ({item['location']})</h4>
                <p style="margin: 0 0 10px 0;"><strong>Match Score:</strong> {item['match_score']}% | <strong>Priority:</strong> {item['strategy']}</p>
                <p style="margin: 0 0 5px 0;"><strong>Generated Documents:</strong></p>
                <ul>
                    <li><a href="{item.get('drive_resume_link', '#')}" target="_blank">Tailored Resume (Google Drive)</a></li>
                </ul>
            </div>
            """

    html_content += """
        <hr/>
        <p style="font-size: 12px; color: #777;">Automated execution by GCP Cloud Run Jobs & Vertex AI.</p>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 Career OS Standup: {len(high_priority)} High Matches, {len(hiring_leads)} Manager DMs & {len(medium_priority)} Medium Matches Today"
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient_email, msg.as_string())
        print(f" Executive briefing emailed successfully to {recipient_email}")
        return True
    except Exception as e:
        print(f"❌ Failed to dispatch email briefing: {str(e)}")
        return False