"""Every piece of LinkedIn DOM knowledge in the project lives in this file.

When LinkedIn reshuffles its markup -- and it does, without warning -- this is
the only file that should need editing. Nothing else in the codebase may contain
a CSS selector or an aria-label.

Strategy: prefer ARIA roles and visible text over class names. LinkedIn's class
names are compiled hashes that change weekly; its aria-labels are driven by
accessibility requirements and are far more stable. Where a class name is
unavoidable it is paired with a role-based fallback.

Each helper returns a Playwright Locator and does NOT wait or assert -- callers
decide what to do when something is missing.
"""
from __future__ import annotations

FEED_URL = "https://www.linkedin.com/feed/"
ME_URL = "https://www.linkedin.com/in/me/"
NOTIFICATIONS_URL = "https://www.linkedin.com/notifications/"
INVITATIONS_SENT_URL = "https://www.linkedin.com/mynetwork/invitation-manager/sent/"

# --- URL builders ----------------------------------------------------------


def hashtag_url(tag: str) -> str:
    return f"https://www.linkedin.com/feed/hashtag/{tag.lstrip('#')}/"


def people_search_url(query: str, page: int = 1) -> str:
    from urllib.parse import quote_plus
    url = f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(query)}"
    if page > 1:
        url += f"&page={page}"
    return url


def content_search_url(query: str) -> str:
    from urllib.parse import quote_plus
    return ("https://www.linkedin.com/search/results/content/"
            f"?keywords={quote_plus(query)}&sortBy=%22date_posted%22")


def profile_url(public_id: str) -> str:
    return f"https://www.linkedin.com/in/{public_id.strip('/')}/"


# --- page-state fingerprints ----------------------------------------------
# Matched against page text (lowercased) by session.py. Order matters only in
# that RESTRICTED is checked before CHALLENGE -- a restriction notice sometimes
# renders inside a checkpoint page.

RESTRICTED_TEXT = [
    "your account has been restricted",
    "we've restricted your account",
    "we have restricted your account",
    "your account is temporarily restricted",
    "account has been suspended",
    "restricted your account from",
]

CHALLENGE_TEXT = [
    "let's do a quick security check",
    "security verification",
    "we need to verify",
    "verify your identity",
    "unusual activity",
    "confirm it's you",
    "enter the code",
    "two-step verification",
    "solve this puzzle",
    "i'm not a robot",
]

LOGGED_OUT_TEXT = [
    "sign in to linkedin",
    "join linkedin",
    "new to linkedin?",
    "sign in with email",
    "welcome back",          # the password-only re-auth screen
]

RATE_LIMIT_TEXT = [
    "you've reached the weekly invitation limit",
    "you have reached the weekly invitation limit",
    "you've reached the maximum number of invitations",
    "try again later",
    "you're browsing too fast",
    "search limit",
    "commercial use limit",
    "monthly limit",
]

RESTRICTED_URLS = ["/checkpoint/rp/restricted", "/checkpoint/lg/restricted"]
CHALLENGE_URLS = ["/checkpoint/challenge", "/checkpoint/rm/", "/captcha"]
LOGGED_OUT_URLS = ["/uas/login", "/login", "/authwall", "/signup"]


# --- composer (our own posts) ---------------------------------------------

def start_post_button(page):
    """The 'Start a post' button on the feed."""
    return page.get_by_role("button", name="Start a post").first


def post_editor(page):
    """The contenteditable inside the share box."""
    return page.locator('div.ql-editor[contenteditable="true"], '
                        'div[role="textbox"][aria-label*="Text editor"]').first


def post_submit_button(page):
    """The 'Post' button in the share dialog. Disabled until text exists."""
    return page.get_by_role("dialog").get_by_role("button", name="Post", exact=True).first


def post_dialog(page):
    return page.get_by_role("dialog").first


# --- feed posts (other people's) ------------------------------------------

def feed_post_cards(page):
    """Every post card currently rendered. The data-* attributes carry the urn."""
    return page.locator("div.feed-shared-update-v2[data-urn], "
                        "div[data-id^='urn:li:activity'], "
                        "div[data-urn^='urn:li:activity']")


def card_see_more(card):
    return card.get_by_role("button", name="see more").first


def card_comment_button(card):
    """Opens the comment box on a specific post."""
    return card.locator('button[aria-label*="Comment"], button:has-text("Comment")').first


def card_comment_editor(card):
    return card.locator('div.ql-editor[contenteditable="true"], '
                        'div[role="textbox"][aria-label*="Text editor"]').first


def card_comment_submit(card):
    return card.locator('button.comments-comment-box__submit-button, '
                        'button:has-text("Reply"), button:has-text("Comment")').last


def card_like_button(card):
    return card.locator('button[aria-label^="React Like"], '
                        'button[aria-label="Like"]').first


def card_author_link(card):
    return card.locator('a[href*="/in/"]').first


def card_author_name(card):
    return card.locator("span.update-components-actor__title span[aria-hidden='true'], "
                        "span.update-components-actor__title").first


def card_author_headline(card):
    return card.locator("span.update-components-actor__description "
                        "span[aria-hidden='true'], "
                        "span.update-components-actor__description").first


def card_age(card):
    """The '2h •' / '3d •' sub-description under the author name."""
    return card.locator("span.update-components-actor__sub-description "
                        "span[aria-hidden='true'], "
                        "span.update-components-actor__sub-description").first


def card_body(card):
    return card.locator("div.update-components-text, "
                        "div.feed-shared-inline-show-more-text").first


def card_reaction_count(card):
    return card.locator("button[aria-label*='reaction'], "
                        "span.social-details-social-counts__reactions-count").first


def card_comment_count(card):
    return card.locator("button[aria-label*='comment'], "
                        "li.social-details-social-counts__comments").first


# --- people search result cards -------------------------------------------

def result_name(card):
    return card.locator("span[aria-hidden='true']").first


def result_headline(card):
    return card.locator("div.entity-result__primary-subtitle, "
                        "div[class*='primary-subtitle']").first


def result_location(card):
    return card.locator("div.entity-result__secondary-subtitle, "
                        "div[class*='secondary-subtitle']").first


# --- connecting -----------------------------------------------------------

def connect_button(page):
    """Primary 'Connect' on a profile page."""
    return page.locator('button[aria-label^="Invite"], '
                        'button:has-text("Connect")').first


def more_actions_button(page):
    """Profiles where Connect is hidden behind 'More'."""
    return page.get_by_role("button", name="More actions").first


def connect_in_more_menu(page):
    return page.locator('div[role="menu"] >> text=Connect').first


def add_note_button(page):
    return page.get_by_role("button", name="Add a note").first


def note_textarea(page):
    return page.locator("textarea#custom-message, textarea[name='message']").first


def send_invite_button(page):
    return page.get_by_role("button", name="Send").first


def send_without_note_button(page):
    return page.get_by_role("button", name="Send without a note").first


def follow_button(page):
    return page.locator('button[aria-label^="Follow"]:not([aria-label*="Following"])').first


# --- search results -------------------------------------------------------

def people_result_cards(page):
    return page.locator("li.reusable-search__result-container, "
                        "div.search-results-container li:has(a[href*='/in/'])")


def result_profile_link(card):
    return card.locator('a[href*="/in/"]').first


# --- own profile (reading + editing) --------------------------------------

def profile_headline(page):
    return page.locator("div.text-body-medium.break-words").first


def profile_name(page):
    return page.locator("h1").first


def profile_location(page):
    return page.locator("span.text-body-small.inline.t-black--light.break-words").first


def profile_current_company(page):
    """The company button in the top card, present on most profiles."""
    return page.locator('button[aria-label*="Current company"], '
                        'div.pv-text-details__right-panel button').first


def profile_top_card(page):
    return page.locator("section:has(h1), main section").first


def profile_about_section(page):
    return page.locator("section:has(div#about), div#about ~ *").first


def edit_intro_button(page):
    return page.locator('button[aria-label*="Edit intro"]').first


def edit_about_button(page):
    return page.locator('button[aria-label*="Edit about"], '
                        'button[aria-label*="Edit summary"]').first


def about_more_button(page):
    """The '…see more' that reveals the full About text when reading."""
    return page.locator('#about ~ * button:has-text("see more"), '
                        'button[aria-label*="see more"]').first


def headline_input(page):
    return page.locator('input[id*="headline"], textarea[id*="headline"]').first


def about_textarea(page):
    return page.locator('textarea[id*="summary"], div[role="textbox"]').first


def save_button(page):
    return page.get_by_role("button", name="Save").first
