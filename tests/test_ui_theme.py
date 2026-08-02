"""The dark theme has to survive two things: the system preference and an
explicit choice that disagrees with it.

A media query alone cannot express "light on a dark phone", so the toggle writes
data-theme on <html> and those selectors override the query in both directions.
The stored choice is applied before the body renders, or the light theme paints
first and flashes.
"""
import re
import unittest
from pathlib import Path

CSS = Path("app/static/styles.css")
BASE = Path("app/templates/base.html")


def _block(css: str, selector: str) -> str:
    i = css.index(selector)
    return css[i:css.index("\n}", i)]


class DarkThemeTokenTests(unittest.TestCase):
    def test_the_media_query_defers_to_an_explicit_light_choice(self) -> None:
        """Without :not([data-theme="light"]) the system preference would win and
        the toggle could never return to light on a dark phone."""
        css = CSS.read_text()

        self.assertIn("@media (prefers-color-scheme: dark)", css)
        self.assertIn(':root:not([data-theme="light"])', css)

    def test_the_toggle_selector_defines_the_same_tokens(self) -> None:
        """The media query and the explicit selector must not drift apart, or the
        theme would look different depending on how it was chosen."""
        css = CSS.read_text()
        from_media = set(re.findall(r"(--[a-z0-9-]+):", _block(css, ':root:not([data-theme="light"])')))
        from_toggle = set(re.findall(r"(--[a-z0-9-]+):", _block(css, ':root[data-theme="dark"] {')))

        self.assertEqual(from_media - {"color-scheme"}, from_toggle)

    def test_every_dark_token_exists_in_the_light_root(self) -> None:
        """A token defined only in dark would fall back to nothing in light."""
        css = CSS.read_text()
        light = set(re.findall(r"(--[a-z0-9-]+):", _block(css, ":root {")))
        dark = set(re.findall(r"(--[a-z0-9-]+):", _block(css, ':root[data-theme="dark"] {')))

        self.assertEqual(dark - light, set())

    def test_the_stylesheet_body_still_holds_no_colour_literals(self) -> None:
        """The point of the token pass: a second theme is values, not rules."""
        css = CSS.read_text()
        body = css[css.index("\nbody {"):]

        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}", body), [])


class ThemeApplicationTests(unittest.TestCase):
    def test_the_stored_choice_is_applied_before_the_body(self) -> None:
        html = BASE.read_text()

        self.assertLess(
            html.index("memoryst-theme"),
            html.index("<body>"),
            "reading the choice after the body renders paints the light theme first",
        )

    def test_reading_the_stored_choice_cannot_break_the_page(self) -> None:
        """localStorage throws in some privacy modes, and this runs in <head> -
        an exception there would stop the document."""
        html = BASE.read_text()
        script = html[html.index("memoryst-theme") - 400:html.index("<body>")]

        self.assertIn("try", script)
        self.assertIn("catch", script)


if __name__ == "__main__":
    unittest.main()
