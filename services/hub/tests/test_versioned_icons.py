from pathlib import Path
import tempfile
import unittest

from orion.api.main import versioned_icons


class VersionedIconsTests(unittest.TestCase):
    def test_icon_urls_carry_the_file_hash_and_change_with_it(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'favicon.ico').write_bytes(b'one')
            (Path(directory) / 'logo-36.png').write_bytes(b'logo')
            page = ('<link rel="icon" href="/owner-assets/favicon.ico"><img src="/owner-assets/logo-36.png" '
                    'srcset="/owner-assets/logo-72.png 2x"><link href="/owner-assets/owner.css?v=2">')
            first = versioned_icons(page, directory)
            self.assertRegex(first, r'href="/owner-assets/favicon\.ico\?v=[0-9a-f]{12}"')
            self.assertRegex(first, r'src="/owner-assets/logo-36\.png\?v=[0-9a-f]{12}"')
            self.assertIn('/owner-assets/logo-72.png 2x', first)       # Missing file: left as it is.
            self.assertIn('/owner-assets/owner.css?v=2', first)          # Not an icon.
            (Path(directory) / 'favicon.ico').write_bytes(b'two')
            self.assertNotEqual(first, versioned_icons(page, directory))


if __name__ == '__main__':
    unittest.main()
