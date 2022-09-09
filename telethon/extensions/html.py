import re
import struct
from collections import deque
from html import escape
from html.parser import HTMLParser
from typing import cast

from .. import helpers
from ..tl.types import (
    MessageEntityBold, MessageEntityItalic,
    MessageEntityCode, MessageEntityPre, MessageEntityEmail, MessageEntityUrl,
    MessageEntityTextUrl, MessageEntityMentionName, MessageEntityMention,
    MessageEntityUnderline, MessageEntityStrike, MessageEntityBlockquote, 
    MessageEntityCustomEmoji, TypeMessageEntity, MessageEntitySpoiler 
    )

# Helpers from markdown.py
def _add_surrogate(text):
    return "".join(
        "".join(chr(y) for y in struct.unpack("<HH", x.encode("utf-16le")))
        if (0x10000 <= ord(x) <= 0x10FFFF)
        else x
        for x in text)


def _del_surrogate(text):
    return text.encode("utf-16", "surrogatepass").decode("utf-16")


class HTMLToTelegramParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.entities = []
        self._building_entities = {}
        self._open_tags = deque()
        self._open_tags_meta = deque()

    def handle_starttag(self, tag, attrs):
        self._open_tags.appendleft(tag)
        self._open_tags_meta.appendleft(None)

        attrs = dict(attrs)
        EntityType = None
        args = {}
        if tag in ["strong", "b"]:
            EntityType = MessageEntityBold
        elif tag in ["em", "i"]:
            EntityType = MessageEntityItalic
        elif tag in ["tg-spoiler"]:
            EntityType = MessageEntitySpoiler
        elif tag == "u":
            EntityType = MessageEntityUnderline
        elif tag in ["del", "s"]:
            EntityType = MessageEntityStrike
        elif tag == "blockquote":
            EntityType = MessageEntityBlockquote
        elif tag == "code":
            try:
                # If we're in the middle of a <pre> tag, this <code> tag is
                # probably intended for syntax highlighting.
                #
                # Syntax highlighting is set with
                #     <code class='language-...'>codeblock</code>
                # inside <pre> tags
                pre = self._building_entities["pre"]
                try:
                    pre.language = attrs["class"][len("language-") :]
                except KeyError:
                    pass
            except KeyError:
                EntityType = MessageEntityCode
        elif tag == "pre":
            EntityType = MessageEntityPre
            args["language"] = ""
        elif tag == "a":
            try:
                url = attrs["href"]
            except KeyError:
                return
            if url.startswith("mailto:"):
                url = url[len("mailto:") :]
                EntityType = MessageEntityEmail
            elif self.get_starttag_text() == url:
                EntityType = MessageEntityUrl
            else:
                EntityType = MessageEntityTextUrl
                args["url"] = url
                url = None
            self._open_tags_meta.popleft()
            self._open_tags_meta.appendleft(url)
        elif tag == "emoji":
            ids = attrs.get("document_id") or attrs.get("id")
            if ids:
                EntityType = MessageEntityCustomEmoji
                args["document_id"] = int(ids)

        if EntityType and tag not in self._building_entities:
            self._building_entities[tag] = EntityType(
                offset=len(self.text),
                # The length will be determined when closing the tag.
                length=0,
                **args,
            )

    def handle_data(self, text):
        previous_tag = self._open_tags[0] if len(self._open_tags) > 0 else ""
        if previous_tag == "a":
            url = self._open_tags_meta[0]
            if url:
                text = url

        for tag, entity in self._building_entities.items():
            entity.length += len(text)

        self.text += text

    def handle_endtag(self, tag):
        try:
            self._open_tags.popleft()
            self._open_tags_meta.popleft()
        except IndexError:
            pass 
        entity = self._building_entities.pop(tag, None)
        if entity:
            self.entities.append(entity)


def parse(html):

    if not html:
        return html, []
    parser = HTMLToTelegramParser()
    parser.feed(_add_surrogate(html))
    text = helpers.strip_text(parser.text, parser.entities)
    return _del_surrogate(text), parser.entities

class TextDecoration():
    def apply_entity(self, entity, text):
        entity_map = {MessageEntityBold: "bold", MessageEntityItalic: "italic", MessageEntitySpoiler: "spoiler", MessageEntityCode: "code", MessageEntityUnderline: "underline", MessageEntityStrike: "strikethrough"}
        if type(entity) in entity_map:
            if re.match(r"^<emoji document_id=\"?\d+?\"?>[^<]*?<\/emoji>$", text):
                return text
            return cast(str, getattr(self, entity_map[type(entity)])(value=text))
        if type(entity) == MessageEntityPre:
            return (
                self.pre_language(value=text, language=entity.language)
                if entity.language
                else self.pre(value=text)
            )
        if type(entity) == MessageEntityMentionName:
            return self.link(value=text, link=f"tg://user?id={entity.user_id}")
        if type(entity) == MessageEntityTextUrl:
            return self.link(value=text, link=cast(str, entity.url))
        if type(entity) == MessageEntityUrl:
            return self.link(value=text, link=text)
        if type(entity) == MessageEntityEmail:
            return self.link(value=text, link=f"mailto:{text}")
        if type(entity) == MessageEntityCustomEmoji:
            return self.custom_emoji(value=text, document_id=entity.document_id)
        return self.quote(text)

    def unparse(self, text, entities = None):
        return "".join(
            self._unparse_entities(
                self._add_surrogates(text),
                sorted(entities, key=lambda item: item.offset) if entities else []))

    def _unparse_entities(
        self, text, entities, offset = None, length =None):
        
        if offset is None:
            offset = 0
        length = length or len(text)

        for index, entity in enumerate(entities):
            if entity.offset * 2 < offset:
                continue
            if entity.offset * 2 > offset:
                yield self.quote(
                    self._remove_surrogates(text[offset : entity.offset * 2]))
            start = entity.offset * 2
            offset = entity.offset * 2 + entity.length * 2

            sub_entities = list(
                filter(lambda e: e.offset * 2 < (offset or 0), entities[index + 1 :]))

            yield self.apply_entity(entity, "".join(self._unparse_entities(text, sub_entities, offset=start, length=offset)))

        if offset < length:
            yield self.quote(self._remove_surrogates(text[offset:length]))

    def _add_surrogates(self, text):
        return text.encode("utf-16-le")
    def _remove_surrogates(self, text):
        return text.decode("utf-16-le")
    def link(self, value, link):
        return f'<a href="{link}">{value}</a>'
    def bold(self, value):
        return f"<b>{value}</b>"
    def italic(self, value):
        return f"<i>{value}</i>"
    def spoiler(self, value):
        return f'<tg-spoiler>{value}</tg-spoiler>'
    def code(self, value):
        return f"<code>{value}</code>"
    def pre(self, value):
        return f"<pre>{value}</pre>"
    def pre_language(self, value, language):
        return f'<pre><code class="language-{language}">{value}</code></pre>'
    def underline(self, value):
        return f"<u>{value}</u>"
    def strikethrough(self, value):
        return f"<s>{value}</s>"
    def quote(self, value):
        return escape(value, quote=False)
    def custom_emoji(self, value, document_id):
        return f'<emoji id={document_id}>{value}</emoji>'

unparse = TextDecoration().unparse
