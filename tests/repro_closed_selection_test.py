import sys
sys.path.insert(0, "tests")
sys.path.insert(0, "src")
from tests.unit.toast.harness import harness
from vendorfake.toast.seed import constants as c

gen = harness()
h = next(gen)

order_body = {
    "entityType": "Order",
    "diningOption": {"guid": c.DINING_OPTION_DINE_IN_GUID, "entityType": "DiningOption"},
    "checks": [{"entityType": "Check", "selections": [{"item": {"guid": c.ITEM_SOUP_GUID, "entityType": "MenuItem"}, "quantity": 1}]}],
}
order = h.post("/orders/v2/orders", order_body).json()
check_guid = order["checks"][0]["guid"]
total = order["checks"][0]["totalAmount"]
print("created check, totalAmount cents:", total, "paymentStatus:", order["checks"][0]["paymentStatus"])

pay = h.post(
    f"/orders/v2/orders/{order['guid']}/checks/{check_guid}/payments",
    [{"type": "OTHER", "amount": total / 100, "otherPayment": {"guid": c.ALT_PAYMENT_EXTERNAL_GUID}}],
)
print("payment status:", pay.status, pay.text[:300])
paid = pay.json()
print("check paymentStatus after OTHER payment:", paid["checks"][0]["paymentStatus"])

resp = h.post(
    f"/orders/v2/orders/{order['guid']}/checks/{check_guid}/selections",
    [{"item": {"guid": c.ITEM_LEMONADE_GUID}, "quantity": 1}],
)
print("add_selections after CLOSED status:", resp.status)
print(resp.text[:500])
