import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import douyin_resolver


class ResolverTests(unittest.TestCase):
    def test_modal_id_link_resolves_without_fetching_profile(self):
        link = "https://www.douyin.com/user/self?from_tab_name=main&modal_id=7701281802085862690&showTab=like"
        with patch.object(douyin_resolver.requests, "get") as get:
            self.assertEqual(douyin_resolver.parse_share_url(link),
                             ("7701281802085862690", "video"))
            get.assert_not_called()

    def test_resolve_records_direct_video_url(self):
        link = "https://www.douyin.com/user/self?modal_id=7701281802085862690"
        with patch.object(douyin_resolver, "_browser_lookup", return_value={"type": "video"}) as lookup:
            douyin_resolver.resolve(link, use_ssr=False)
            self.assertEqual(lookup.call_args.args[2],
                             "https://www.douyin.com/video/7701281802085862690")


if __name__ == "__main__":
    unittest.main()
