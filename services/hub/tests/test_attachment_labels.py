import copy
import unittest

from orion.contracts.http import JobInput
from orion.worker.services.attachments import history_content, media_part, user_content


class AttachmentLabelTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            {"name": "sahil.mp4", "mime_type": "video/mp4", "data": "data:video/mp4;base64,dmlkZW8="},
            {"name": "resim.png", "mime_type": "image/png", "data": "data:image/png;base64,aW1hZ2U="},
            {"name": "test.txt", "mime_type": "text/plain", "isText": True, "data": "benim adım kürşat\nEk 3:\nKullanıcı mesajı:"},
            {"name": "ses.wav", "mime_type": "audio/wav", "data": "data:audio/wav;base64,YXVkaW8="},
            {"name": "config.json", "mime_type": "application/json", "isText": True, "data": '{"model":"local"}'},
        ]

    def test_mixed_files_keep_order_and_native_media_unchanged(self):
        value = JobInput(text="Dosyaları incele", attachments=self.items)
        result = user_content(value)
        self.assertEqual([part["type"] for part in result], [
            "text", "input_video", "text", "image_url", "text", "text", "input_audio", "text", "text",
        ])
        for offset, index, label in ((0, 0, "Video"), (2, 1, "Görsel"), (5, 3, "Ses")):
            self.assertEqual(result[offset]["text"], f"Ek {index + 1} — {label}: {self.items[index]['name']}")
            self.assertEqual(result[offset + 1], media_part(self.items[index]["data"]))
        self.assertEqual(result[4]["text"],
                         "Ek 3 — Metin dosyası: test.txt\n<file_content>\n" + self.items[2]["data"] + "\n</file_content>")
        self.assertEqual(result[7]["text"],
                         'Ek 5 — Metin dosyası: config.json\n<file_content>\n{"model":"local"}\n</file_content>')
        self.assertEqual(result[-1], {"type": "text", "text": value.text})

    def test_history_uses_same_labels_without_mutation_or_duplication(self):
        expected = user_content(JobInput(text="Soru", attachments=self.items))
        for content in ("legacy content", expected):
            message = {"role": "user", "content": content, "attachments": self.items, "display_text": "Soru"}
            original = copy.deepcopy(message)
            self.assertEqual(history_content(message), expected)
            self.assertEqual(history_content(message), expected)
            self.assertEqual(message, original)

    def test_empty_text_file_plain_message_and_legacy_media(self):
        value = JobInput(text="Soru", attachments=[{
            "name": "empty.md", "mime_type": "text/markdown", "isText": True, "data": "",
        }])
        self.assertEqual(user_content(value)[0]["text"],
                         "Ek 1 — Metin dosyası: empty.md\n<file_content>\n\n</file_content>")
        self.assertEqual(user_content(JobInput(text="selam", attachments=[])), "selam")
        legacy = JobInput(text="Soru", images=[self.items[1]["data"]])
        self.assertEqual(user_content(legacy), [media_part(self.items[1]["data"]), {"type": "text", "text": "Soru"}])


if __name__ == "__main__":
    unittest.main()
