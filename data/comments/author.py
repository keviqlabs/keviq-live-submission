"""Author data/comments/{cards,labels}.jsonl from the hand-written rows below: python data/comments/author.py

Each row's code fixes its label (and an order row its order fields) when the comment is written, before any
model sees it. No model sets or edits gold. The first FOUNDER_SAMPLE comments are the Founder's review sample.
An order row may end with the action the shop expects next (ask the customer, or wait for the seller); an
`other` row may end with one too (confirm a cancel with the customer, or do nothing).
"""

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOUNDER_SAMPLE = 30
# The Founder confirmed all FOUNDER_SAMPLE labels on 27 October 2026, none changed
FOUNDER_STATUS = "founder_approved"
# expected_action: ASK = the comment leaves size or item open, ask the customer rather than guess;
# SELLER = a discount request or an amount over orders/rules.py SELLER_THRESHOLD_VND, the seller decides
ASK, SELLER = "ask_customer", "needs_seller"
# On `other` rows (contract v1 has no cancel label): CANCEL = the customer asks to cancel their one earlier order,
# the shop asks them to confirm before cancelling; NONE = mentions cancelling but asks for nothing to be done
CANCEL, NONE = "confirm_cancel", "none"
ACTIONS = (ASK, SELLER, CANCEL, NONE)
CAT = {c["sku"]: c for c in json.loads((ROOT / "data/catalog.json").read_text())}

# (card_id, sku, live_s); card-03 is retired 0.5 s before card-04 goes live (quick switch, C7)
CARDS = [
    ("card-01", "GIAY-01", 2),
    ("card-02", "TUI-03", 70),
    ("card-03", "BOOT-02", 140),
    ("card-04", "BOOT-05", 200),
    ("card-05", "AO-07", 270),
    ("card-06", "VAY-01", 340),
    ("card-07", "QUAN-01", 410),
    ("card-08", "CAO-01", 480),
]
END_S = 560
RETIRE = {"card-03": 199.5}

# Row: (code, text[, kind, card, size, color, qty[, sku][, ASK | SELLER]]) or (code, text[, CANCEL | NONE]) ; codes: O order, P price, S stock, C complaint,
# X spam, F scam, F! scam with a spelled-out phone, N other, L = order that arrives late (C7)
W = {}
W[1] = [
    ("N", "Chào shop, mới vào live"),
    ("P", "Đôi này giá bao nhiêu vậy shop?"),
    ("S", "Size 40 còn không ạ"),
    ("O", "Chốt 1 đôi size 39 nha shop", "current_card", "card-01", "39", None, 1),
    ("F", "Chúc mừng bạn trúng iPhone, nhắn 0900 000 001 để nhận quà"),
    ("O", "I'll take one in size 38", "current_card", "card-01", "38", None, 1),
    ("X", "Ghé shop em bên kia xem đồ si nha mọi người"),
    ("P", "How much for the white sneakers?"),
    ("O", "lấy 2 đôi size 40", "current_card", "card-01", "40", None, 2),
    ("C", "Đơn tuần trước giao thiếu 1 đôi mà chưa ai trả lời"),
    ("N", "Shop có bán online trên web không"),
    ("S", "Màu trắng còn đủ size không shop"),
    ("O", "chốt GIAY-01 size 40", "explicit_sku", "card-01", "40", None, 1),
    ("N", "Đẹp quá trời"),
    ("O", "Cho mình một đôi nha, size 39, sđt 0900 000 012", "current_card", "card-01", "39", None, 1),
    ("X", "follow chéo không mọi người 🔥🔥🔥"),
    ("F", "Admin shop đây, inbox Zalo 0900 000 002 để được giảm 50%"),
    ("P", "đôi sneaker này bao tiền v"),
    ("O", "lấy đôi này", "current_card", "card-01", None, None, 1, ASK),
    ("S", "size 41 có không"),
    ("F", "Nhận 2 triệu tiền thưởng, bấm http://nhan-qua-tang.example/vn ngay"),
    ("O", "Chốt cái áo hôm qua shop live nha", "unresolved", None, None, None, 1, ASK),
    ("C", "Live lag quá, hình bị đứng hoài"),
    ("L", "sneaker trắng size 38 x1", "current_card", "card-01", "38", "trắng", 1),
    ("F", "Phí ship đơn của bạn chưa thanh toán, chuyển 30k qua http://ship-fee.example/pay để nhận hàng"),
]
W[2] = [
    ("N", "túi xinh ghê"),
    ("P", "Túi này nhiêu tiền shop"),
    ("O", "Lấy đôi sneaker vừa rồi size 40 nha", "previous_card", "card-01", "40", None, 1),
    ("O", "chốt 1 túi", "current_card", "card-02", None, None, 1),
    ("S", "túi đỏ còn hàng không"),
    ("F", "Shop ơi mình là bên vận chuyển, đơn bị giữ ở kho, gọi 0900 000 003 để xử lý"),
    ("O", "Cho mình 1 đôi sneaker trắng size 39 nữa", "explicit_sku", "card-01", "39", "trắng", 1),
    ("O", "đôi giày lúc nãy còn size 39 không, lấy 1 đôi", "previous_card", "card-01", "39", None, 1),
    ("O", "Lấy 3 cái túi này nha shop", "current_card", "card-02", None, None, 3, SELLER),
    ("C", "Lần trước mua túi bị bong da sau 1 tuần"),
    ("P", "price for this bag?"),
    ("O", "lấy 2 cái túi này", "current_card", "card-02", None, None, 2),
    ("F", "Bạn được chọn nhận voucher 1 triệu, nhập thông tin tại www.voucher-tri-an.example"),
    ("S", "Có màu đen không shop"),
    ("O", "Cho mình lấy cái túi, sđt 0900 000 013 nha", "current_card", "card-02", None, None, 1),
    ("N", "Hello from Đà Nẵng 👋"),
    ("O", "the sneakers before this, size 38 please", "previous_card", "card-01", "38", None, 1),
    ("X", "Ai cần acc game ib mình"),
    ("F", "Inbox mình để nhận quà tri ân 5 triệu, chỉ cần chuyển phí 200k"),
    ("P", "túi da thật không shop, giá bao nhiêu"),
    ("S", "Túi còn mấy cái vậy shop"),
    ("O", "Chốt món số 5 nha", "unresolved", None, None, None, 1),
    ("C", "Shop rep tin nhắn chậm quá, nhắn từ hôm qua"),
    ("F", "Tài khoản của bạn bị khoá, xác minh tại http://fb-xac-minh.example/login"),
    ("O", "ok chốt nha shop", "current_card", "card-02", None, None, 1),
]
W[3] = [
    ("P", "Boot này giá sao shop"),
    ("O", "chốt boot size 39", "current_card", "card-03", "39", None, 1),
    ("O", "Lấy cái túi đỏ lúc nãy nha shop", "previous_card", "card-02", None, "đỏ", 1),
    ("S", "boot size 38 còn không"),
    ("F", "Chuyển khoản trước 100k để giữ hàng, liên hệ 0900 000 004 nhé mọi người"),
    ("O", "2 đôi size 40", "current_card", "card-03", "40", None, 2),
    ("N", "boot đẹp xỉu"),
    ("O", "the bag from before, one please", "previous_card", "card-02", None, None, 1),
    ("X", "Like page bên mình nhận quà nhé 🎁🎁"),
    ("C", "Giao hàng lần trước bị trễ 5 ngày"),
    ("F", "Mình là admin nhóm, ai muốn mua rẻ hơn liên hệ 0900 000 005"),
    ("P", "Is this real leather? How much?"),
    ("O", "Lấy sneaker trắng size 38 nha", "explicit_sku", "card-01", "38", "trắng", 1),
    ("S", "còn mấy đôi size 39 vậy"),
    ("O", "chốt", "current_card", "card-03", None, None, 1),
    ("F", "Link giảm giá 90% chỉ hôm nay: http://giam-gia-soc.example"),
    ("O", "lấy BOOT-02 size 38", "explicit_sku", "card-03", "38", None, 1),
    ("N", "Shop nói chậm lại xíu được không"),
    ("X", "Ủng hộ kênh mình với mọi người ơi"),
    ("P", "boot này bao nhiêu vậy"),
    ("C", "Hàng nhận về khác hình"),
    ("F", "Nhận quà miễn phí, gửi số tài khoản ngân hàng cho mình qua tin nhắn"),
    ("L", "Cho mình đôi boot size 40, sđt 0900 000 014", "current_card", "card-03", "40", None, 1),
    ("S", "Boot có size 41 không shop"),
    ("O", "Lấy cái váy hôm trước", "unresolved", None, None, None, 1),
]
W[4] = [
    ("O", "Lấy đôi boot đen vừa rồi size 39", "previous_card", "card-03", "39", "đen", 1),
    ("P", "Đôi nâu này giá bao nhiêu"),
    ("O", "chốt đôi nâu size 38", "current_card", "card-04", "38", "nâu", 1),
    ("F", "Bạn đã trúng thưởng 10 triệu, liên hệ 0900 000 006 trước 12h"),
    ("S", "Boot nâu còn size 40 không"),
    ("O", "the black boots just now, size 40 please", "previous_card", "card-03", "40", "đen", 1),
    ("N", "Màu nâu này đẹp hơn màu đen"),
    ("X", "Bán sỉ giày dép giá xưởng, ai cần ib"),
    ("O", "lấy 1 đôi này size 39", "current_card", "card-04", "39", None, 1),
    ("C", "Shop ghi giá sai trên bài đăng hôm qua"),
    ("F", "Khuyến mãi tri ân www.tri-an-khach-hang.example nhập mã để nhận 500k"),
    ("P", "How much are the brown boots?"),
    ("S", "còn size 38 không shop"),
    ("O", "Lấy túi da đỏ, 1 cái", "explicit_sku", "card-02", None, "đỏ", 1),
    ("F", "Shop chính hãng chỉ bán qua số 0900 000 007, các nơi khác là giả"),
    ("O", "chốt 2 đôi", "current_card", "card-04", None, None, 2),
    ("O", "Chốt 1 đôi size 40, bớt 50k được không shop", "current_card", "card-04", "40", None, 1, SELLER),
    ("X", "Ai mua mỹ phẩm ib em nha"),
    ("P", "giá đôi này sao vậy shop"),
    ("C", "Đặt hàng 3 lần không thấy shop xác nhận"),
    ("P", "Mua 2 đôi có rẻ hơn không shop"),
    ("S", "Hàng có sẵn hay phải đặt?"),
    ("O", "lấy cái giống bạn ở trên", "unresolved", None, None, None, 1),
    ("O", "Đôi boot nâu size 40, giao Quận 3, sđt 0900 000 015", "current_card", "card-04", "40", "nâu", 1),
    ("O", "lấy đôi đen lúc nãy, 2 đôi size 38", "previous_card", "card-03", "38", "đen", 2),
]
W[5] = [
    ("P", "áo khoác này bao nhiêu shop"),
    ("O", "Lấy áo size M", "current_card", "card-05", "M", None, 1),
    ("O", "Đôi boot nâu vừa rồi size 39 còn không, lấy 1", "previous_card", "card-04", "39", "nâu", 1),
    ("S", "size L còn không"),
    ("F", "Nhắn 0900 000 008 để nhận mã giảm giá độc quyền"),
    ("O", "size như bạn trên nha shop", "current_card", "card-05", None, None, 1, ASK),
    ("O", "I'll take this jacket in L", "current_card", "card-05", "L", None, 1),
    ("X", "Sub kênh YouTube mình nha mọi người"),
    ("C", "Áo mua lần trước giặt bị phai màu"),
    ("O", "lấy 2 áo size L", "current_card", "card-05", "L", None, 2),
    ("F", "Tham gia nhóm kín săn sale tại http://nhom-kin-sale.example"),
    ("P", "price?"),
    ("S", "Có size S không shop"),
    ("O", "the boots before this one, size 40", "previous_card", "card-04", "40", None, 1),
    ("F", "Đơn hàng của bạn bị hoàn, vui lòng thanh toán lại tại www.hoan-don.example"),
    ("O", "Lấy boot đen size 40", "explicit_sku", "card-03", "40", "đen", 1),
    ("N", "Shop ở đâu vậy"),
    ("X", "Nhận thiết kế logo giá rẻ"),
    ("P", "áo jean này nhiêu"),
    ("C", "Đợi hoài không thấy shop gửi mã vận đơn"),
    ("S", "Áo có sẵn để lấy liền không shop"),
    ("S", "áo còn hàng không"),
    ("L", "chốt áo này, sđt 0900 000 016", "current_card", "card-05", None, None, 1),
    ("O", "lấy cái quần hôm bữa", "unresolved", None, None, None, 1),
    ("O", "lấy món vừa rồi", "previous_card", "card-04", None, None, 1),
]
W[6] = [
    ("O", "Lấy váy size S", "current_card", "card-06", "S", None, 1),
    ("P", "Váy này giá bao nhiêu ạ"),
    ("O", "cái áo khoác vừa rồi size M, 1 cái", "previous_card", "card-05", "M", None, 1),
    ("S", "váy còn size M không"),
    ("F", "Bạn là khách may mắn, nhận quà tại 0900 000 009"),
    ("N", "váy xinh quá"),
    ("O", "I'll take the dress, size M", "current_card", "card-06", "M", None, 1),
    ("X", "Ghé shop mình bán phụ kiện nhé"),
    ("C", "Lần trước váy bị sứt chỉ"),
    ("O", "lấy 2 váy size S", "current_card", "card-06", "S", None, 2),
    ("F", "Vay tiền nhanh không thế chấp, liên hệ 0900 000 010"),
    ("P", "how much is this dress"),
    ("S", "có màu khác không shop"),
    ("O", "the jacket just now in L please", "previous_card", "card-05", "L", None, 1),
    ("F", "Xác nhận đơn hàng tại http://xac-nhan-don.example để không bị huỷ"),
    ("O", "Lấy túi đỏ 1 cái", "explicit_sku", "card-02", None, "đỏ", 1),
    ("N", "Mọi người mua gì chưa"),
    ("X", "Bán acc Netflix giá rẻ"),
    ("P", "váy nhiêu tiền"),
    ("C", "Shop hứa tặng quà mà không thấy"),
    ("F!", "không chín không không, không không không, không một một — gọi số này nhận quà"),
    ("S", "Váy còn mấy cái vậy shop"),
    ("L", "chốt váy size M, sđt 0900 000 017", "current_card", "card-06", "M", None, 1),
    ("O", "Lấy cái kia luôn", "unresolved", None, None, None, 1),
    ("O", "đặt thêm cái áo lúc nãy size M", "previous_card", "card-05", "M", None, 1),
]
W[7] = [
    ("P", "quần này giá bao nhiêu"),
    ("O", "Lấy quần size 29", "current_card", "card-07", "29", None, 1),
    ("O", "Lấy váy hoa vừa rồi size S", "previous_card", "card-06", "S", None, 1),
    ("S", "size 30 còn không shop"),
    ("F", "Nhận ngay 1 triệu khi đăng ký tại www.dang-ky-nhan-tien.example"),
    ("N", "quần đẹp á"),
    ("O", "two pairs of jeans, size 28", "current_card", "card-07", "28", None, 2),
    ("X", "Mọi người ghé page bên em ủng hộ nha"),
    ("C", "Lần trước quần bị rách sau 2 lần mặc"),
    ("O", "chốt size 30", "current_card", "card-07", "30", None, 1),
    ("F", "Mình bên CSKH shop, gọi 0900 000 018 để xác nhận đơn"),
    ("P", "jeans price pls"),
    ("S", "Quần có size 31 không"),
    ("O", "the dress before, size M", "previous_card", "card-06", "M", None, 1),
    ("F", "Mã giảm giá 70% tại http://ma-giam-gia.example/ok"),
    ("O", "Lấy sneaker trắng size 39", "explicit_sku", "card-01", "39", "trắng", 1),
    ("N", "Bao giờ shop live tiếp vậy"),
    ("X", "👍👍👍👍👍👍"),
    ("N", "đừng huỷ đơn em nha shop", NONE),  # c169, u014: order c009
    ("C", "Shop giao nhầm size cho mình"),
    ("F", "Bạn được hoàn tiền 300k, đọc mã OTP vừa gửi về máy cho mình để nhận"),
    ("S", "còn hàng không shop ơi"),
    ("L", "lấy quần size 28, số 0900 000 019", "current_card", "card-07", "28", None, 1),
    ("O", "Lấy cái số 2 nha", "unresolved", None, None, None, 1),
    ("O", "món lúc nãy lấy 1 cái size M", "previous_card", "card-06", "M", None, 1),
]
W[8] = [
    ("P", "Giày cao gót này giá sao"),
    ("O", "Lấy đôi đỏ size 37", "current_card", "card-08", "37", "đỏ", 1),
    ("O", "Cái quần vừa rồi size 29, 1 cái", "previous_card", "card-07", "29", None, 1),
    ("S", "Màu đen còn size 36 không"),
    ("F", "Chúc mừng! Tài khoản của bạn nhận 5 triệu, gọi 0900 000 020"),
    ("N", "Cao gót đẹp quá"),
    ("O", "black pair, size 38 please", "current_card", "card-08", "38", "đen", 1),
    ("X", "Nhận order hàng Quảng Châu, ib em"),
    ("C", "Đơn lần trước chưa được hoàn tiền"),
    ("O", "lấy 2 đôi đen size 36", "current_card", "card-08", "36", "đen", 2),
    ("F", "Đăng nhập để nhận quà: http://dang-nhap-qua.example"),
    ("P", "how much for the heels?"),
    ("S", "size 38 màu đỏ còn không"),
    ("O", "the jeans just now, size 30", "previous_card", "card-07", "30", None, 1),
    ("F", "Liên hệ kho sỉ 0900 000 021 để mua giá gốc"),
    ("O", "Lấy CAO-01 màu đỏ size 36", "explicit_sku", "card-08", "36", "đỏ", 1),
    ("N", "Shop ơi huỷ giúp em đôi size 39 nha", CANCEL),  # c192, u065: one earlier order, c032 size 39
    ("X", "Mời mọi người tham gia minigame bên page em"),
    ("P", "đôi này nhiêu tiền"),
    ("C", "Live hôm nay không có tiếng"),
    ("F", "Nạp 100k nhận 1 triệu, nhắn tin cho mình để biết cách"),
    ("S", "Còn đôi nào size 37 không shop"),
    ("O", "chốt đôi đen size 37, sđt 0900 000 022", "current_card", "card-08", "37", "đen", 1),
    ("O", "lấy cái áo len xám", "explicit_sku", None, None, "xám", 1, "AO-09"),
    ("O", "lấy món trước đó size 28", "previous_card", "card-07", "28", None, 1),
]
CODE = {
    "O": "order",
    "L": "order",
    "P": "price_question",
    "S": "stock_question",
    "C": "complaint",
    "X": "spam",
    "F": "scam",
    "F!": "scam",
    "N": "other",
}
PHONE = re.compile(r"0900 000 0\d\d")
LINK = re.compile(r"https?://|www\.|\.example\b")


def card_events():
    out = []
    for cid, sku, live_s in CARDS:
        c = CAT[sku]
        variants = [dict(v) for v in c["variants"]]
        base = {
            "session_id": "comments200",
            "source": "sim",
            "v": 1,
            "type": "card",
            "card_id": cid,
            "sku": sku,
            "name": c["name"],
            "price": c["price"],
            "currency": "VND",
            "variants": variants,
            "stock": sum(v["stock"] for v in variants),
            "image_url": f"sim://comments/{cid}.png",
        }
        ts = int(live_s * 1000)
        out.append(
            {
                "event_id": f"k-{cid}-v1",
                "ts_ms": ts,
                **base,
                "version": 1,
                "status": "live",
                "provenance": {"frame_ts_ms": ts, "speech_event_ids": [], "edited_by": None},
            }
        )
        if cid in RETIRE:
            rts = int(RETIRE[cid] * 1000)
            out.append(
                {
                    "event_id": f"k-{cid}-v2",
                    "ts_ms": rts,
                    **base,
                    "version": 2,
                    "status": "retired",
                    "provenance": {"frame_ts_ms": ts, "speech_event_ids": [], "edited_by": None},
                }
            )
    return sorted(out, key=lambda e: e["ts_ms"])


def rows():
    out, n = [], 0
    lives = [c[2] for c in CARDS] + [END_S]
    for w in range(1, 9):
        start, end = lives[w - 1] + 2, lives[w] - 2
        step = (end - start) / len(W[w])
        for i, row in enumerate(W[w]):
            n += 1
            code, text = row[0], row[1]
            ts = int((start + i * step) * 1000)
            r = {
                "comment_id": f"c{n:03d}",
                "ts_ms": ts,
                "user_ref": f"u{(n * 37) % 160 + 1:03d}",
                "lang": "en" if re.fullmatch(r"[\x00-\x7f👋🔥🎁👍]*", text) else "vi",
                "text": text,
                "label": CODE[code],
                "label_source": FOUNDER_STATUS if n <= FOUNDER_SAMPLE else "template",
                "has_phone": bool(PHONE.search(text)) or code == "F!",
                "has_link": bool(LINK.search(text)),
                "order": None,
            }
            if code == "L":
                r["arrives_ms"] = int((lives[w] + 3) * 1000)
            tail = row[7:] if code in ("O", "L") else row[2:]
            if action := next((x for x in tail if x in ACTIONS), None):
                r["expected_action"] = action
            if code in ("O", "L"):
                kind, card, size, color, qty = row[2:7]
                extra = [x for x in tail if x not in ACTIONS]
                sku = extra[0] if extra else (dict((c[0], c[1]) for c in CARDS)[card] if card else None)
                r["order"] = {
                    "sku": sku,
                    "size": size,
                    "color": color,
                    "qty": qty,
                    "reference_kind": kind,
                    "referenced_card_id": card,
                }
            out.append(r)
    return out


def main():
    cards, labels = card_events(), rows()
    (ROOT / "data/comments").mkdir(exist_ok=True)
    (ROOT / "data/comments/cards.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in cards)
    )
    (ROOT / "data/comments/labels.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in labels)
    )
    print(len(labels), Counter(r["label"] for r in labels))
    print("kinds", Counter(r["order"]["reference_kind"] for r in labels if r["order"]))
    print(
        "phone",
        sum(r["has_phone"] for r in labels),
        "link",
        sum(r["has_link"] for r in labels),
        "late",
        sum("arrives_ms" in r for r in labels),
        "en",
        sum(r["lang"] == "en" for r in labels),
    )
    print(
        "scam with contact", sum(r["label"] == "scam" and (r["has_phone"] or r["has_link"]) for r in labels)
    )
    print(
        "expected_action", {r["comment_id"]: r["expected_action"] for r in labels if "expected_action" in r}
    )


if __name__ == "__main__":
    main()
