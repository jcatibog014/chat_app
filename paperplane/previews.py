"""Link previews: the title and description of the first web address in a message.

The SERVER fetches someone else's page here, so this file carries Pulse
Check's private-network guard (SSRF):

1. The address must be http(s), with a server name, no password in it, and
   not a name that plainly means "inside" (localhost, *.internal ...).
2. We look the name up OURSELVES, keep only public addresses (allowed_ip:
   no private, loopback, link-local like the cloud's metadata service at
   169.254.169.254, carrier-grade NAT, reserved...), and connect to THAT
   address. Letting a library look the name up again could get a different,
   private answer the second time (DNS rebinding).
3. Every redirect (at most 3) is checked the same way.
4. We read at most 256 KB, wait at most 5 seconds, accept only text/html,
   and never send cookies or anything about the person.

It runs in a small pool of threads AFTER the message is committed (the
sender's message never waits for someone else's slow website). When it
finds a title it saves a LinkPreview and sends "message.updated", so the
card appears under the message in every open tab.
"""
import http.client
import ipaddress
import logging
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from . import db, outbox
from .dates import utcnow
from .formatting import TRAILING, URL
from .models import Conversation, LinkPreview, Message

log = logging.getLogger(__name__)

MAX_BYTES = 256 * 1024
TIMEOUT = 5.0
MAX_REDIRECTS = 3
USER_AGENT = "PaperPlane/1.0 (link previews)"
PRIVATE_ENDINGS = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
REDIRECTS = (301, 302, 303, 307, 308)
POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="link-preview")


class Refused(Exception):
    """We won't (or can't) fetch this page."""


# ---------- The guard ----------

def allowed_ip(ip_text, allow_private=False):
    try:
        ip = ipaddress.ip_address(ip_text.split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped  # ::ffff:10.0.0.1 is really 10.0.0.1
    if ip.is_multicast or ip.is_unspecified:
        return False
    return True if allow_private else ip.is_global


def url_problem(url, allow_private=False):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "not a web address"
    if parts.username or parts.password:
        return "has a password in it"
    try:
        parts.port  # noqa: B018  (a port like :99999 raises)
    except ValueError:
        return "a bad port"
    host = parts.hostname.lower().rstrip(".")
    if not allow_private:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None and not allowed_ip(host):
            return "a private address"
        if literal is None and (host == "localhost" or host.endswith(PRIVATE_ENDINGS)):
            return "a private name"
    return None


# ---------- Fetching ----------

def fetch(url, allow_private=False, resolver=socket.getaddrinfo, timeout=TIMEOUT):
    """(final address, HTML text) of a public web page. Raises Refused."""
    for hop in range(MAX_REDIRECTS + 1):
        problem = url_problem(url, allow_private)
        if problem:
            raise Refused(problem)
        parts = urlsplit(url)
        https = parts.scheme == "https"
        host = parts.hostname
        port = parts.port or (443 if https else 80)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        try:
            answers = resolver(host, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError):
            raise Refused("name not found") from None
        allowed = [answer[4][0] for answer in answers if allowed_ip(answer[4][0], allow_private)]
        if not allowed:
            raise Refused("only private addresses")
        ip = allowed[0]
        sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect((ip, port))  # THAT address, never the name again
            if https:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            connection = (http.client.HTTPSConnection if https else http.client.HTTPConnection)(host, port, timeout=timeout)
            connection.sock = sock
            connection.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": "text/html",
                                                     "Accept-Language": "en", "Connection": "close"})
            response = connection.getresponse()
            location = response.getheader("Location")
            if response.status in REDIRECTS and location:
                if hop == MAX_REDIRECTS:
                    raise Refused("too many redirects")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise Refused(f"status {response.status}")
            content_type = (response.getheader("Content-Type") or "").lower()
            if "text/html" not in content_type:
                raise Refused("not a web page")
            raw = response.read(MAX_BYTES)
        except Refused:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as error:
            raise Refused(f"connection: {type(error).__name__}") from None
        finally:
            sock.close()
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        try:
            return url, raw.decode(charset, errors="replace")
        except LookupError:
            return url, raw.decode("utf-8", errors="replace")
    raise Refused("too many redirects")


# ---------- Reading the page ----------

class MetaReader(HTMLParser):
    """Reads <title> and the <meta> tags a page offers for previews (Open Graph)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = {}
        self.in_title = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        attrs = {name.lower(): (value or "") for name, value in attrs}
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in ("og:title", "og:description", "og:site_name", "description", "twitter:title") and attrs.get("content"):
                self.found.setdefault(key, attrs["content"])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title and len(self.title) < 500:
            self.title += data


def tidy(text, limit):
    text = " ".join((text or "").split())
    return (text[:limit - 3] + "...") if len(text) > limit else (text or None)


def read_page(html):
    reader = MetaReader()
    try:
        reader.feed(html)
    except Exception:  # a broken page: keep what we read so far
        pass
    found = reader.found
    return {
        "title": tidy(found.get("og:title") or found.get("twitter:title") or reader.title, 200),
        "description": tidy(found.get("og:description") or found.get("description"), 300),
        "site_name": tidy(found.get("og:site_name"), 100),
    }


def first_url(text):
    for match in URL.finditer(text or ""):
        url = match.group(0)
        while url and url[-1] in TRAILING:
            if url[-1] == ")" and url.count("(") >= url.count(")"):
                break
            url = url[:-1]
        if len(url) >= 12:
            return url[:2000]
    return None


# ---------- Making a preview ----------

def make_preview(app, message_id, fetcher=fetch):
    """Fetch, read, save, and tell the chat. Safe to call for any message (it does nothing
    when there's no address, no title, or the message is gone)."""
    from . import tools
    with app.app_context():
        s = db.new_session(app)
        try:
            message = s.get(Message, message_id)
            if message is None or message.deleted_at is not None:
                return None
            url = first_url(message.body)
            if not url or s.query(LinkPreview).filter_by(message_id=message_id).first():
                return None
            try:
                final_url, html = fetcher(url, allow_private=app.config.get("ALLOW_PRIVATE_TARGETS", False))
            except Refused as why:
                log.info("No preview for %s: %s", url, why)
                return None
            page = read_page(html)
            if not page["title"]:
                return None
            site = page["site_name"] or urlsplit(final_url).hostname
            preview = LinkPreview(message_id=message.id, url=url, site_name=tidy(site, 100), title=page["title"],
                                  description=page["description"], fetched_at=utcnow())
            s.add(preview)
            s.flush()
            s.refresh(message)  # it may have been edited meanwhile
            if message.deleted_at is not None:
                s.rollback()
                return None
            tools.changed(s, s.get(Conversation, message.conversation_id), message, utcnow())
            s.commit()
            return preview
        except Exception:
            s.rollback()
            log.exception("Link preview for message %s failed", message_id)
            return None
        finally:
            s.close()


def notice(s, conversation, message, moment):
    """chats.after_send_hooks: a message with an address gets a preview, after it is saved."""
    app = s.info.get("app")
    if app is None or not app.config.get("PREVIEWS") or not first_url(message.body):
        return
    outbox.after_commit(s, lambda: POOL.submit(make_preview, app, message.id))


def previews_for(s, message_ids):
    found = {}
    if message_ids:
        for preview in s.query(LinkPreview).filter(LinkPreview.message_id.in_(message_ids)):
            found[preview.message_id] = preview
    return found


def init_app(app):
    from . import chats
    if notice not in chats.after_send_hooks:
        chats.after_send_hooks.append(notice)
