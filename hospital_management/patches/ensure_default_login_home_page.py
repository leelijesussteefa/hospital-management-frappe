import frappe

def execute():
    """Ensure Website Settings doctype does not force a custom home page over /login."""
    ws = frappe.get_doc("Website Settings", "Website Settings")
    changed = False

    # Ensure home_page does not force a custom home page or break native login
    if ws.home_page and ws.home_page != "":
        ws.home_page = ""
        changed = True

    # Filter out any conflicting root redirects
    valid_redirects = []
    for r in ws.get("route_redirects", []):
        src = (r.source or "").strip()
        if src in ("/", "/$", "^/$", "^$") and (r.target or "").strip() != "/login":
            changed = True
        else:
            valid_redirects.append(r)

    if changed:
        ws.route_redirects = valid_redirects
        ws.save(ignore_permissions=True)
        frappe.db.commit()
