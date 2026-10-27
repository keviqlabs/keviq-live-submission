"""Author the C5 comment streams (test1 played 5 times): python data/comments/c5/author_c5.py

stream-k.jsonl holds 100 synthetic comments for loop k (ts_ms from the loop start, 0 <= ts_ms < 351500) with the
gold fixed here by code, before any model sees them (label_source "template", like data/comments/author.py).
messenger-k.jsonl is the customer side of Messenger for loop k: which reply to send, after which shop message.

The product windows come from the local test1 gold (data/local/test1, not committed):
    python data/comments/c5/author_c5.py windows PATH/TO/test1/labels.json
copies only segment bounds, product codes and when a fixed price was said into windows.json. The streams are built
from windows.json alone, so the committed files carry product codes and times, nothing heard in the video.
No randomness: rerunning gives the same files. With the local gold present (C5_GOLD or the default path) the run
also checks the orders against its stock and prices.
"""

import json
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LOCAL_GOLD = ROOT / "data/local/test1/labels.json"
STREAMS = range(1, 6)
LOOP_MS = 351500
# "this item": window start + 8 s to window end - 5 s (H p95 ~ 4.7 s plus the card); "just now": 60 s after a switch
NOW_IN, NOW_OUT, AGO_MS = 8000, 5000, 60000
SELLER_THRESHOLD = 2_000_000  # orders/rules.py sends a bigger total to the seller
ASK, SELLER, CONFIRM = "ask_customer", "needs_seller", "confirm_order"
CANCEL, NONE = "confirm_cancel", "none"
PHONE = re.compile(r"0900 000 0\d\d")
LINK = re.compile(r"https?://|www\.|\.example\b")
DIGITS = "không một hai ba bốn năm sáu bảy tám chín".split()

# fmt: off
SIZES = {"SP01": ["40", "41"], "SP02": ["39", "40"], "SP03": ["41", "42"], "SP04": ["M", "L"],
         "SP05": ["M", "L"], "SP06": ["L", "XL"]}  # SP07-SP10 come in one size
UNIT = {"SP01": "đôi", "SP02": "đôi", "SP03": "đôi"}  # every other product is "cái"

# Orders by appearance (0..10 = SP01, SP02, ..., SP10, SP01 again) and size; qty 1 unless given.
NORMAL = [(0, "40"), (1, "40"), (1, "39"), (2, "41"), (2, "42"), (3, "L"), (3, "M"), (3, "L"), (4, "L"),
          (4, "L"), (5, "L"), (6, None), (6, None), (7, None), (8, None), (8, None), (9, None), (10, "40")]
PREVIOUS = [(1, "41"), (2, "40"), (3, "41"), (4, "M"), (5, "M"), (6, "XL")]  # size of the product shown before
DISCOUNT = {1: (5, "L"), 2: (3, "L"), 3: (5, "L"), 4: (3, "L"), 5: (5, "L")}
OVER_STOCK = {1: (10, "40", 3), 2: (6, None, 3), 3: (9, None, 2), 4: (3, "L", 4), 5: (10, "40", 3)}
# Situation roles, each on its own customer. 10: "ok" after the card's price changed; 5: late; 12: confirmed.
SIT10 = {1: "SP01", 2: "SP04", 3: "SP09", 4: "SP01", 5: "SP04"}
LATE = ["SP02", "SP03", "SP04", "SP06", "SP07", "SP09"]
SIT12 = {1: ("SP02", "SP03"), 2: ("SP03", "SP07"), 3: ("SP02", "SP06"), 4: ("SP02", "SP07"), 5: ("SP03", "SP07")}
SIT8 = ["SP03", "SP02", "SP06", "SP04", "SP05", "SP07"]  # a real cancel, confirmed in Messenger
SIT7 = ["SP05", "SP07", "SP02", "SP06", "SP08", "SP03"]  # "don't cancel": nothing to do
PHONE_ORDER = ["SP04", "SP05", "SP02", "SP06", "SP03"]
YES_TEXT = {1: "ok", 2: "Oke", 3: "dc", 4: "Đồng ý", 5: "okie"}
SPELLED_SEP = {1: " ", 2: ", ", 3: " ", 4: " - ", 5: ", "}

ORDER_SIZED = [
    "Chốt {u} này size {s} nha shop", "Lấy 1 {u} size {s} nhé", "Em lấy size {s} một {u}",
    "size {s} 1 {u} cho mình nha", "Cho em 1 {u} size {s} ạ", "Mình chốt size {s} nha",
    "Đặt 1 {u} {s} nha shop ơi", "Lên đơn size {s} cho em", "Chốt đơn {u} này, size {s}",
    "Em mua 1 {u} size {s} nhé shop", "{s} một {u} nha", "Lấy {u} này size {s} cho chị",
    "Size {s} chốt luôn shop ơi", "Cho mình xin 1 {u} size {s}", "I'll get one in {s}",
    "One in size {s} please", "Mình lấy size {s} nha, 1 {u} thôi", "Chốt 1 size {s} giúp em",
    "Ghi đơn cho em size {s} nha", "Em chốt size {s} ạ", "Để cho mình 1 {u} size {s} nhé",
    "Lấy liền size {s} nha shop",
]
ORDER_ONE = [
    "Chốt 1 cái này nha shop", "Lấy cái này cho em", "Em lấy 1 cái nhé", "Cho mình 1 cái này ạ",
    "Mình chốt cái đang giới thiệu nha", "Đặt 1 cái nha shop ơi", "Lên đơn cái này cho em với",
    "Chốt luôn 1 cái", "Em mua cái này nhé", "Ghi cho mình 1 cái", "I'll take this one",
    "One of these for me please", "Lấy 1 cái giống trên live nha", "Cho chị xin 1 cái này",
    "Mình đặt 1 cái nha", "Chốt cái này giúp em ạ", "Để cho em 1 cái nhé shop", "1 cái này nha shop",
]
ORDER_PREVIOUS = [  # "vừa nãy" first: most of the 20 just-now orders say it
    "Cho em {u} vừa nãy size {s} nha", "Shop ơi {u} vừa nãy cho em 1 size {s}", "Món vừa nãy size {s} chốt 1 nha",
    "{u} vừa nãy đẹp quá, lấy 1 size {s}", "Vừa nãy {u} đó size {s} chốt giúp em",
    "Quay lại {u} vừa nãy được không, em lấy size {s}", "Lấy {u} vừa nãy size {s} nhé shop",
    "Em chốt món vừa nãy, size {s}", "{u} vừa nãy còn size {s} thì em lấy 1", "Mình lấy {u} vừa nãy, size {s} nha",
    "Cho chị 1 {u} vừa nãy size {s}", "Đặt {u} vừa nãy giúp em, size {s}", "Món vừa nãy cho em size {s} ạ",
    "Lấy {u} hồi nãy size {s} nhé shop", "Mình lấy {u} giới thiệu lúc nãy, size {s}",
    "Em muốn {u} ban nãy size {s} ạ", "the one before, size {s} please",
]
ORDER_MISSING = [
    "Chốt 1 {u} đang cầm nha shop", "Lấy {u} này cho em nhé", "Em đặt 1 {u} trên tay shop",
    "Cho mình 1 {u} giống trên live", "Mình lấy 1 {u} này ạ", "Lên đơn {u} này giúp em",
    "I want this one", "Chốt luôn {u} đang thử", "Ghi đơn 1 {u} này cho chị", "Em mua {u} này nha",
    "Để em 1 {u} này nhé", "Đặt {u} này nha shop ơi",
]
ORDER_DISCOUNT = [
    "Lấy 1 {u} size {s}, bớt cho em 20k nha", "Size {s} 1 {u}, shop bớt chút được không",
    "Chốt size {s} nếu giảm 10k", "Em lấy {u} size {s}, bớt 30k được không shop",
    "Size {s} một {u}, giảm cho em ít nha",
]
ORDER_OVER = [
    "Lấy {q} {u}{sz} nha shop", "Chốt {q} {u}{sz} cho em", "Cho mình {q} {u}{sz} luôn", "Em đặt {q} {u}{sz} nhé",
    "Gom {q} {u}{sz} cho chị",
]
ORDER_PHONE = [
    "Chốt 1 {u} size {s} nha, sđt 0900 000 0{xx}", "Lấy size {s} cho em, số 0900 000 0{xx}",
    "Em đặt 1 {u} {s}, liên hệ 0900 000 0{xx}", "size {s} 1 {u}, sdt em 0900 000 0{xx}",
    "Order one in {s}, phone 0900 000 0{xx}",
]
ORDER_UNRESOLVED = [  # items that are not in test1
    "Lấy cho em cái túi đeo chéo hôm qua nha", "Chốt 1 cái mũ lưỡi trai như live trước",
    "Shop ơi em lấy cái ví màu kem hôm trước", "Đặt 1 khăn choàng như buổi live tuần rồi",
    "Em lấy cái balo hôm bữa shop đăng", "Cho mình 1 thắt lưng như bài đăng hôm qua",
    "I'll take the scarf from last week's live", "Chốt cái ô gấp shop đăng hôm trước",
    "Lấy 1 kính mát như tuần trước nha", "Em đặt cái nón len hôm qua shop live",
]
PRICE = [
    "{u} này giá sao shop?", "Giá bao nhiêu vậy ạ", "Bao nhiêu tiền 1 {u} vậy shop", "Shop báo giá {u} này với",
    "how much is this one?", "Giá {u} này nhiêu thế", "Mua 2 {u} có giá tốt hơn không shop",
    "Ship về Cần Thơ mất phí bao nhiêu", "Shop ơi {u} này nhiêu", "Giá trên thẻ là giá cuối chưa shop",
    "Có freeship không shop, giá bao nhiêu", "What's the price for this?", "{u} này mấy trăm vậy shop",
    "Giá {u} này có kèm hộp không ạ", "Bao nhiêu 1 {u} thế shop ơi", "Cho em xin giá {u} đang cầm",
    "Báo giá giúp em với ạ", "{u} này nhiêu k vậy", "Giá có bao ship chưa ạ", "Mua 1 {u} hết bao nhiêu tiền",
    "Giá sỉ {u} này sao shop", "Cho hỏi giá {u} này với", "Tổng tiền 1 {u} cả ship là bao nhiêu",
    "Shop để giá {u} này giúp em", "Giá lẻ 1 {u} bao nhiêu shop", "price of this one?",
]
STOCK_SIZED = [
    "Size {s} còn không shop", "Còn size {s} không ạ", "{u} này còn size {s} chứ", "Size {s} hết chưa shop",
    "Shop ơi size {s} còn mấy {u}", "do you have size {s}?", "Còn {s} không vậy shop", "Size {s} còn hàng không",
    "{s} còn không shop ơi", "Em hỏi size {s} còn không ạ", "is size {s} still available?",
    "Size {s} có sẵn không shop",
]
STOCK_ONE = [
    "{u} này còn hàng không shop", "Còn mấy {u} vậy shop", "Hết hàng chưa shop ơi", "still in stock?",
    "Còn {u} này không ạ", "{u} này còn nhiều không shop", "Shop còn hàng {u} này chứ", "Có sẵn hàng không shop",
]
COMPLAINT = [
    "Đơn tháng trước giao chậm cả tuần", "Hàng về bị móp hộp shop ơi", "Nhắn shop từ sáng chưa thấy trả lời",
    "Live bị giật quá không xem được", "Tiếng nhỏ quá shop ơi", "Lần trước giao sai màu cho mình",
    "Áo mua tuần rồi bị bung chỉ ở tay", "Shop chưa gửi mã vận đơn cho em", "Đơn trước thiếu quà tặng kèm",
    "Giày lần trước đi 2 hôm bị bong đế", "Hình mờ quá không thấy rõ", "Chờ hoàn tiền 2 tuần rồi chưa thấy",
    "Mic rè quá shop", "Gọi hotline không ai bắt máy", "Lần trước đóng gói sơ sài, hàng bị bẩn",
    "the stream keeps freezing", "my last order arrived damaged", "Shop trả lời tin nhắn chậm ghê",
    "Đơn của em giao trễ 1 tuần rồi", "Hàng nhận khác màu trên live", "Đơn đổi size gửi đi 10 ngày chưa nhận lại",
    "Shop hứa tặng quà mà không thấy đâu", "Live hay bị mất tiếng giữa chừng", "Phí ship lần trước tính sai",
    "Túi đóng gói lần trước bị rách",
]
SPAM = [
    "Ghé page em xem đồ handmade nha", "Follow mình nhé mọi người", "Ai cần dịch vụ chạy quảng cáo ib",
    "Bán sim số đẹp giá rẻ", "Tuyển cộng tác viên bán hàng online", "Mọi người qua kênh mình xem review nha",
    "🔥🔥🔥🔥", "Nhận may đo áo dài, ai cần ib", "Like dạo ủng hộ nhau nha", "check out my channel",
    "Ai cần in áo nhóm liên hệ em", "Nhận chụp ảnh sản phẩm giá mềm", "Mọi người ơi ghé tiệm trà sữa em ủng hộ",
    "Bán đồ ăn vặt, ai ăn ib", "Sub chéo không ạ", "Nhận dạy trang điểm cá nhân, ib mình", "follow for follow",
    "Ghé live bên em đang xả kho nè", "Ai cần sticker dễ thương ib nha", "Bán cây cảnh mini, ai cần ib",
    "Nhận thiết kế logo giá rẻ", "Ai cần học tiếng Anh online ib mình", "Tiệm bánh nhà em nhận đặt bánh kem",
    "Nhận sửa điện thoại tại nhà", "Bên em cho thuê váy cưới", "Mọi người like page mình với",
    "Nhận làm thiệp cưới handmade", "Ghé kênh em xem nấu ăn nha",
]
SCAM_PHONE = [
    "Bạn trúng quà tri ân, gọi 0900 000 0{xx} để nhận", "Shop thật chỉ dùng số 0900 000 0{xx}, đừng tin chỗ khác",
    "Nhắn Zalo 0900 000 0{xx} nhận voucher 300k", "Bên giao hàng đây, đơn bị kẹt, gọi 0900 000 0{xx}",
    "Cần vốn gấp liên hệ 0900 000 0{xx} duyệt trong 5 phút", "Đăng ký làm CTV nhận lương ngày, gọi 0900 000 0{xx}",
    "Kho sỉ chính hãng, giá gốc tại 0900 000 0{xx}", "Quà sinh nhật page, nhắn 0900 000 0{xx} để lấy mã",
    "You won a gift card, call 0900 000 0{xx}", "Hoàn tiền đơn lỗi, liên hệ 0900 000 0{xx} ngay",
]
SCAM_SPELLED = [
    "Nhận thưởng gọi {p}", "Số admin: {p}, nhắn để nhận mã", "Gọi ngay {p} nhận 1 triệu",
    "Kết bạn zalo {p} để vào nhóm quà", "Hỗ trợ đơn hàng xin gọi {p}",
]
SCAM_LINK = [  # 6 templates x 10 domains: the 30 links over 5 loops never repeat
    "Nhận quà miễn phí tại https://{d}.example/qua", "Bấm vào www.{d}.example để nhận voucher",
    "Xác minh tài khoản tại http://{d}.example/login kẻo bị khoá",
    "Đơn bị giữ, thanh toán phí tại https://{d}.example/pay", "Săn deal 1k ở {d}.example nhanh tay",
    "Claim your prize at https://{d}.example",
]
SCAM_DOMAINS = ["qua-tri-an", "voucher-hot", "nhan-thuong-ngay", "kho-deal", "check-don-hang", "xac-minh-tk",
                "phi-ship", "freeship-vip", "hoan-tien-nhanh", "mini-game-qua"]
SCAM_DM = [
    "Inbox mình nhận quà 200k miễn phí", "Ai muốn nhận voucher 500k ib em gửi link",
    "Chuyển cọc 50k để giữ suất quà nhé, ib mình", "Mình là admin page, ib để được giá nội bộ",
    "Gửi mã OTP cho mình để nhận hoàn tiền", "Kiếm 1 triệu mỗi ngày tại nhà, ib để biết cách",
    "Ai cần vay nhanh không cần giấy tờ ib", "Tặng máy tính bảng cho 10 bạn đầu tiên inbox",
    "Nạp 50k nhận 500k, nhắn riêng mình", "DM me for free gift cards", "Gửi số tài khoản để nhận tiền thưởng nhé",
    "Bạn được hoàn 200k, ib mình làm thủ tục", "Nhóm kín săn hàng hiệu giá 1k, nhắn mình để vào",
    "Đầu tư lãi 30% một tuần, ib tư vấn", "Mình bên vận chuyển, ib để xác nhận địa chỉ nhận quà",
    "Ib mình để nhận thẻ cào 100k", "Ai cần việc làm thêm 500k/ngày inbox",
    "Nhắn riêng để nhận quà từ nhãn hàng", "Bạn may mắn được chọn, ib để nhận thưởng",
]
OTHER = [
    "Chào cả nhà, mới vô live", "Live hôm nay đông ghê", "Shop xinh quá", "Hello shop, xem từ Huế nè",
    "Mình đang xem trên điện thoại nè", "Đồ đẹp mà rẻ ghê", "Mai shop có live không", "Shop live mấy giờ tới",
    "Trời mưa ngồi xem live chill ghê", "Mấy giờ shop chốt sổ vậy", "Có ai ở Sài Gòn không", "watching from Hanoi",
    "nice stream", "Shop dễ thương quá", "Ánh sáng hôm nay đẹp ghê", "Lần đầu xem live của shop",
    "Đồ shop lúc nào cũng xinh", "Có ai mua giống mình không", "Mới đi làm về kịp xem live",
    "Shop nói chuyện vui ghê", "Xem live mà muốn mua hết", "Chúc shop đông khách", "Tối nay ai xem cùng không",
    "good evening everyone", "Cả nhà ăn tối chưa", "Hóng món tiếp theo",
]
DONT_CANCEL = [  # Vietnamese only: orders/intent reads English "don't cancel" as a cancel
    "Shop đừng huỷ đơn của em nha", "Em vẫn lấy, đừng hủy nhé shop", "Không huỷ đâu shop, giữ đơn cho em",
    "Shop ơi khỏi huỷ, em vẫn mua", "Đơn em đừng huỷ nha, mai em nhận",
]
REAL_CANCEL = [
    "Shop ơi cho em huỷ đơn vừa đặt", "Huỷ giúp mình đơn ban nãy nha shop", "Em hủy đơn nhé, đặt nhầm rồi",
    "please cancel my order, sorry", "Thôi em không lấy nữa, shop huỷ giúp em",
]
# Once every template is used, a text gets one of these ("^" = in front) so no line repeats.
VARIANTS = {
    "order": {"vi": ["^Shop ơi, ", " nha", " nhé", " ạ", " luôn", " với"], "en": [" please", "^Hi, ", " thanks"]},
    "question": {"vi": ["^Shop ơi, ", "^Cho em hỏi, ", "^Ơi shop, ", " ạ", " vậy"],
                 "en": ["^Hi, ", "^Quick question: ", " thanks"]},
    "other": {"vi": ["^Mọi người ơi, ", " nha", " nè", " ạ", " luôn"], "en": ["^Hey, ", " :)", " lol"]},
}
# fmt: on


def norm(text: str) -> str:
    t = " ".join(unicodedata.normalize("NFC", text).lower().split())
    return re.sub(r"[\s.,!?…]+$", "", t)


def spelled(xx: str, k: int) -> str:
    words = [DIGITS[int(d)] for d in "09000000" + xx]
    return SPELLED_SEP[k].join(" ".join(g) for g in (words[:4], words[4:7], words[7:]))


def vary(text: str, v: str) -> str:
    if v.startswith("^"):
        keep = text.startswith(("I ", "I'")) or text[:1].isdigit() or text[:2].isupper()
        return v[1:] + (text if keep else text[0].lower() + text[1:])
    return text[:-1] + v + "?" if text.endswith("?") else text + v


class Picker:
    """Rotates through a bank; a text already used (here or in data/comments/labels.jsonl) gets a variant."""

    def __init__(self, taken):
        self.used, self.cursor = {norm(t) for t in taken}, Counter()

    def claim(self, text):
        if norm(text) in self.used:
            raise ValueError(f"text already used: {text}")
        self.used.add(norm(text))
        return text

    def __call__(self, name, bank, kind="order", **kw):
        start, texts = self.cursor[name], []
        for a in range(len(bank)):
            tpl = bank[(start + a) % len(bank)]
            t = tpl.format(**kw)
            texts.append((a, t[0].upper() + t[1:] if tpl[0] == "{" else t))
        for step in range(1 + max(len(v) for v in VARIANTS[kind].values())):
            for a, t in texts:
                options = ["", *VARIANTS[kind]["en" if t.isascii() else "vi"]]
                if (
                    step < len(options)
                    and norm(text := vary(t, options[step]) if step else t) not in self.used
                ):
                    self.cursor[name] = (start + a + 1) % len(bank)
                    return self.claim(text)
        raise ValueError(f"bank {name} ran out of texts for {kw}")


def unit(product):
    return UNIT.get(product, "cái")


def appearances(win):
    """Each time a product is on screen: its now window [lo, hi] and, after a switch, its just-now window."""
    segs, apps = win["segments"], []
    for j, s in enumerate(segs):
        if not s["product"]:
            continue
        next_band = segs[j + 1]["band_ms"][0] if j + 1 < len(segs) else win["loop_ms"]
        lo, hi = s["from_ms"] + NOW_IN, min(s["to_ms"] - NOW_OUT, next_band)
        prev = apps[-1]["product"] if apps else None
        ago = (lo, min(hi, s["from_ms"] + AGO_MS)) if prev else None
        apps.append({"i": len(apps), "product": s["product"], "from": s["from_ms"], "lo": lo, "hi": hi,
                     "prev": prev, "ago": ago, "next_from": None})  # fmt: skip
    for a, b in zip(apps, apps[1:], strict=False):
        a["next_from"] = b["from"]
    return apps


def spread(lo, hi, n):
    return [int(lo + (i + 0.5) * (hi - lo) / n) for i in range(n)]


def order_row(app, size, qty, kind, action, roles=(), product=None):
    product = product or (app["product"] if app else None)
    return {"label": "order", "app": app["i"] if app else None, "roles": set(roles), "expected_action": action,
            "order": {"product": product, "size": size, "color": None, "qty": qty, "reference_kind": kind},
            "sit": [3] if product else []}  # fmt: skip


def build(k, win, pick):
    """(comments, messenger lines) for loop k."""
    apps = appearances(win)
    said = win["price_changes"]
    gaps = [s for s in win["segments"] if not s["product"]]
    xx = iter(f"{10 * k + j:02d}" for j in range(10))

    # Orders. Normal ones are qty 1 at the card price, within stock and under the seller threshold.
    normal = [order_row(apps[i], s, 1, "current_card", CONFIRM) for i, s in NORMAL]

    def take(role, products, last=False, need=None):
        free = [
            o
            for o in normal
            if not o["roles"] and o["order"]["product"] in products and (not need or need(o))
        ]
        if not free:
            raise ValueError(f"loop {k}: no free order for {role} in {products}")
        o = free[-1] if last else min(free, key=lambda o: products.index(o["order"]["product"]))
        o["roles"].add(role)
        return o

    p10 = SIT10[k]
    app10 = next(a["i"] for a in apps if a["product"] == p10 and a["lo"] <= said[p10][0] <= a["hi"])
    take("sit10", [p10], need=lambda o: o["app"] == app10)
    for p in (LATE[(2 * k - 2) % 6], LATE[(2 * k - 1) % 6]):
        take("late", [p], last=True)
    for p in SIT12[k]:
        take("sit12", [p])
    take("sit8", SIT8, need=lambda o: o["order"]["size"])
    take("sit7", SIT7)
    take("phone", PHONE_ORDER)

    previous = []
    for j in range(4):
        i, s = PREVIOUS[(4 * (k - 1) + j) % 6]
        previous.append(order_row(apps[i], s, 1, "previous_card", CONFIRM, ["previous"], apps[i]["prev"]))
    di, ds = DISCOUNT[k]
    discount = order_row(apps[di], ds, 1, "current_card", SELLER, ["discount"])
    oi, os_, oq = OVER_STOCK[k]
    over = order_row(apps[oi], os_, oq, "current_card", SELLER, ["over"])
    missing = [
        order_row(apps[(4 * (k - 1) + j) % 6], None, 1, "current_card", ASK, ["missing"]) for j in range(4)
    ]
    unresolved = [order_row(None, None, 1, "unresolved", ASK, ["unresolved"]) for _ in range(2)]
    orders = normal + previous + [discount, over] + missing + unresolved

    for o in orders:
        p, s, roles = o["order"]["product"], o["order"]["size"], o["roles"]
        kw = {"u": unit(p), "s": s}
        if "unresolved" in roles:
            o["text"] = pick("unresolved", ORDER_UNRESOLVED)
        elif "over" in roles:
            o["text"] = pick(
                "over", ORDER_OVER, q=o["order"]["qty"], u=unit(p), sz=f" size {s}" if s else " này"
            )
        elif "discount" in roles:
            o["text"] = pick("discount", ORDER_DISCOUNT, **kw)
        elif "missing" in roles:
            o["text"] = pick("missing", ORDER_MISSING, **kw)
        elif "previous" in roles:
            o["text"] = pick("previous", ORDER_PREVIOUS, **kw)
        elif "phone" in roles:
            o["text"] = pick("phone", ORDER_PHONE, xx=next(xx), **kw)
            o["sit"].append(9)
        elif s:
            o["text"] = pick("sized", ORDER_SIZED, **kw)
        else:
            o["text"] = pick("one", ORDER_ONE)

    # Times. Now orders spread over their window (the over-stock one last); late ones at its very end.
    by_app = {}
    for o in normal + [discount, over] + missing:
        if "late" not in o["roles"]:
            by_app.setdefault(o["app"], []).append(o)
    for i, group in by_app.items():
        a = apps[i]
        group.sort(key=lambda o: ("sit10" not in o["roles"], "over" in o["roles"]))
        hi = a["hi"] - 2000 if a["hi"] - a["lo"] >= 10000 else a["hi"]
        for o, ts in zip(group, spread(a["lo"], hi, len(group)), strict=True):
            o["ts_ms"] = ts
    for o in normal:
        a = apps[o["app"]]
        if "late" in o["roles"]:
            o["ts_ms"], o["arrives_ms"] = a["hi"] - 750, a["next_from"] + 5000
            o["sit"].append(5)
        if "sit10" in o["roles"]:  # in CONFIRM_SENT before the fixed price is said
            o["ts_ms"] = min(o["ts_ms"], said[p10][0] - 8000)
            assert o["ts_ms"] >= a["lo"], (k, o)
            o["sit"].append(10)
        if "sit12" in o["roles"]:
            o["sit"].append(12)
    for o in previous:
        lo, hi = apps[o["app"]]["ago"]
        o["ts_ms"] = (lo + hi) // 2 + 250
    for n, o in enumerate(unresolved):
        g = gaps[(k - 1 + n) % 3]
        o["ts_ms"] = int(g["from_ms"] + 1500 + (0.2 + 0.12 * (k - 1)) * (g["to_ms"] - g["from_ms"] - 3000))
    rows = list(orders)

    # Situations 8 and 7: a comment from the customer of an earlier order
    for role, bank, delay, action in (
        ("sit8", REAL_CANCEL, 25000, CANCEL),
        ("sit7", DONT_CANCEL, 30000, NONE),
    ):
        o = next(o for o in normal if role in o["roles"])
        n = int(role[3:])
        o["sit"].append(n)
        ts = o["ts_ms"] + delay + 3000 * (k - 1)
        assert ts < LOOP_MS - 2000, (k, role)
        rows.append({"label": "other", "order": None, "expected_action": action, "roles": {role}, "of": o,
                     "sit": [n], "ts_ms": ts, "text": pick.claim(bank[k - 1])})  # fmt: skip

    # Price and stock questions on the products with a now window of 10 s or more
    qapps = [a for a in apps if a["hi"] - a["lo"] >= 10000]
    questions = {}
    for j in range(26):
        a = qapps[(j + k) % len(qapps)]
        p = a["product"]
        if j % 2 == 0:
            row = {"label": "price_question", "text": pick("price", PRICE, "question", u=unit(p)), "sit": []}
        elif sizes := SIZES.get(p):
            s = sizes[(j // 2 + k) % len(sizes)]
            row = {
                "label": "stock_question",
                "text": pick("stock", STOCK_SIZED, "question", s=s, u=unit(p)),
                "sit": [4],
            }
        else:
            row = {
                "label": "stock_question",
                "text": pick("stock1", STOCK_ONE, "question", u=unit(p)),
                "sit": [4],
            }
        questions.setdefault(a["i"], []).append(row | {"order": None})
    for i, group in questions.items():
        for row, ts in zip(group, spread(apps[i]["lo"], apps[i]["hi"], len(group)), strict=True):
            row["ts_ms"] = ts + 400
        rows += group

    # Scam, spam, complaint and chat across the loop
    loose = [{"label": "scam", "text": pick("scam_phone", SCAM_PHONE, "other", xx=next(xx)), "sit": [9]}
             for _ in range(2)]  # fmt: skip
    p = spelled(next(xx), k)
    loose.append(
        {"label": "scam", "text": pick("scam_spelled", SCAM_SPELLED, "other", p=p), "sit": [9], "spelled": 1}
    )
    for n in range(6):
        g = 6 * (k - 1) + n
        loose.append(
            {"label": "scam", "text": pick.claim(SCAM_LINK[g % 6].format(d=SCAM_DOMAINS[g % 10])), "sit": []}
        )
    loose += [{"label": "scam", "text": pick("scam_dm", SCAM_DM, "other"), "sit": []} for _ in range(6)]
    for label, bank, n in (("spam", SPAM, 10), ("complaint", COMPLAINT, 9), ("other", OTHER, 8)):
        loose += [{"label": label, "text": pick(label, bank, "other"), "sit": []} for _ in range(n)]
    for label, phase in (("scam", 0.11), ("spam", 0.47), ("complaint", 0.29), ("other", 0.73)):
        group = [r for r in loose if r["label"] == label]
        ph = (phase + 0.13 * k) % 1
        for n, r in enumerate(group):
            r["ts_ms"], r["order"] = 2000 + int((n + ph) / len(group) * 347000), None
    rows += loose

    # Order by time (ties 1 ms apart), then ids and users
    rows.sort(key=lambda r: r["ts_ms"])
    for a, b in zip(rows, rows[1:], strict=False):
        b["ts_ms"] = max(b["ts_ms"], a["ts_ms"] + 1)
    for n, r in enumerate(r for r in rows if r["label"] == "order"):
        r["user_ref"] = f"k{k}-u{n + 1:02d}"
    others = (r for r in rows if r["label"] != "order")
    for n, r in enumerate(others):
        r["user_ref"] = r["of"]["user_ref"] if "of" in r else f"k{k}-u{31 + (n * 7) % 30:02d}"
    out = []
    for n, r in enumerate(rows):
        r["comment_id"] = f"c5-{k}-{n + 1:03d}"
        text = r["text"]
        e = {
            "comment_id": r["comment_id"],
            "ts_ms": r["ts_ms"],
            "user_ref": r["user_ref"],
            "lang": "en" if re.fullmatch(r"[\x00-\x7f👋🔥🎁👍]*", text) else "vi",
            "text": text,
            "label": r["label"],
            "label_source": "template",
            "has_phone": bool(PHONE.search(text)) or bool(r.get("spelled")),
            "has_link": bool(LINK.search(text)),
            "order": r["order"],
        }
        if "expected_action" in r:
            e["expected_action"] = r["expected_action"]
        if "arrives_ms" in r:
            e["arrives_ms"] = r["arrives_ms"]
        e["situations"] = sorted(r["sit"])
        out.append(e)

    # Messenger: what the customer sends, after which shop message
    lines = []
    for o in normal:
        base = {"user_ref": o["user_ref"], "order_comment_id": o["comment_id"]}
        if "sit12" in o["roles"]:
            lines.append(base | {"comment_id": o["comment_id"], "quick_reply": "CONFIRM", "text": None,
                                 "after": CONFIRM, "not_before_ms": o["ts_ms"] + 10000, "after_price_change": None,
                                 "situation": 12, "expect": "CONFIRMED"})  # fmt: skip
        if "sit10" in o["roles"]:
            lines.append(base | {"comment_id": o["comment_id"], "quick_reply": None, "text": YES_TEXT[k],
                                 "after": CONFIRM, "not_before_ms": said[p10][1] + 15000, "after_price_change": p10,
                                 "situation": 10, "expect": "NEEDS_SELLER"})  # fmt: skip
        if "sit8" in o["roles"]:
            c = next(r for r in rows if r.get("of") is o and "sit8" in r["roles"])
            lines.append(base | {"comment_id": c["comment_id"], "quick_reply": "CANCEL", "text": None,
                                 "after": "ask_cancel", "not_before_ms": c["ts_ms"] + 10000,
                                 "after_price_change": None, "situation": 8, "expect": "CANCELLED"})  # fmt: skip
    lines.sort(key=lambda m: m["not_before_ms"])
    return out, [{"msg_id": f"c5-{k}-m{n + 1:02d}"} | m for n, m in enumerate(lines)]


def generate(win, taken):
    """{k: (comments, messenger lines)} for the five loops; taken = texts no line may repeat."""
    pick = Picker(taken)
    return {k: build(k, win, pick) for k in STREAMS}


def windows(gold):
    """What the streams need from the local test1 gold: segment bounds, products, when a fixed price was said."""

    def ms(s):
        return int(round(s * 1000))

    return {
        "loop_ms": LOOP_MS,
        "segments": [
            {"from_ms": ms(s["from_s"]), "to_ms": ms(s["to_s"]), "product": s["product"],
             "band_ms": [ms(b) for b in s["change_band_s"] or []]}
            for s in gold["segments"]
        ],
        "price_changes": {p["product"]: [ms(t) for t in p["said_s"]] for p in gold["price_changes"]},
    }  # fmt: skip


def check_stock(gold, streams):
    """Problems (empty = none) with the order gold against the test1 stock and prices: per loop, the orders that
    should go through hold no more than the stock, and the over-stock order asks for more than there is."""
    problems = []
    for k, rows in streams.items():
        held = Counter()
        for r in rows:
            o = r["order"]
            if not o or not o["product"] or r["expected_action"] == ASK:
                continue
            prod = gold["products"][o["product"]]
            key = (o["product"], o["size"] or "one")
            stock = prod["stock"].get(key[1])
            if stock is None:
                problems.append(f"{r['comment_id']}: no stock for {key}")
            elif o["qty"] > 1:
                if o["qty"] <= stock or r["expected_action"] != SELLER:
                    problems.append(f"{r['comment_id']}: qty {o['qty']} vs stock {stock}")
            else:
                held[key] += 1
                if prod["price_first"] > SELLER_THRESHOLD:
                    problems.append(f"{r['comment_id']}: over the seller threshold")
        problems += [f"loop {k}: {key} holds {n}" for key, n in held.items()
                     if n > gold["products"][key[0]]["stock"][key[1]]]  # fmt: skip
    return problems


def local_gold():
    path = Path(os.environ.get("C5_GOLD") or LOCAL_GOLD)
    return json.loads(path.read_text()) if path.exists() else None


def main(argv):
    if argv[:1] == ["windows"]:
        gold = json.loads(Path(argv[1]).read_text()) if len(argv) > 1 else local_gold()
        if not gold:
            sys.exit("windows: give the path to the local test1 labels.json")
        (HERE / "windows.json").write_text(json.dumps(windows(gold), indent=1) + "\n")
        print("wrote windows.json")
        return
    win = json.loads((HERE / "windows.json").read_text())
    taken = [json.loads(line)["text"] for line in (ROOT / "data/comments/labels.jsonl").open()]
    streams = {}
    for k, (rows, lines) in generate(win, taken).items():
        streams[k] = rows
        (HERE / f"stream-{k}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        )
        (HERE / f"messenger-{k}.jsonl").write_text(
            "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in lines)
        )
        print(k, len(rows), dict(Counter(r["label"] for r in rows)), "messenger", len(lines))
    every = [r for rows in streams.values() for r in rows]
    print("all", len(every), dict(Counter(r["label"] for r in every)))
    print("situations", dict(sorted(Counter(s for r in every for s in r["situations"]).items())))
    print("reference_kind", dict(Counter(r["order"]["reference_kind"] for r in every if r["order"])))
    print("expected_action", dict(Counter(r["expected_action"] for r in every if "expected_action" in r)))
    print("en", sum(r["lang"] == "en" for r in every), "late", sum("arrives_ms" in r for r in every))
    if gold := local_gold():
        print("stock check", check_stock(gold, streams) or "ok")


if __name__ == "__main__":
    main(sys.argv[1:])
