from conftest import add_item, signup

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100   # 작은 가짜 JPEG 바이트


def test_add_list_update_delete(client):
    headers = signup(client)
    item = add_item(client, headers, color="네이비", name="니트")
    assert item["category"] == "상의" and item["photo_url"] is None

    items = client.get("/api/wardrobe", headers=headers).json()
    assert [i["id"] for i in items] == [item["id"]]

    r = client.patch(f"/api/wardrobe/{item['id']}", headers=headers, json={"category": "아우터"})
    assert r.json()["category"] == "아우터"

    assert client.delete(f"/api/wardrobe/{item['id']}", headers=headers).status_code == 204
    assert client.get("/api/wardrobe", headers=headers).json() == []


def test_photo_is_saved_as_file(client):
    headers = signup(client)
    r = client.post("/api/wardrobe", headers=headers, data={"category": "하의"},
                    files={"photo": ("pants.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 201
    url = r.json()["photo_url"]
    assert url.startswith("/media/") and url.endswith(".jpg")
    assert client.get(url).content == JPEG


def test_rejects_non_image_and_large_photo(client):
    headers = signup(client)
    txt = client.post("/api/wardrobe", headers=headers, data={"category": "하의"},
                      files={"photo": ("a.txt", b"hello", "text/plain")})
    assert txt.status_code == 415
    big = client.post("/api/wardrobe", headers=headers, data={"category": "하의"},
                      files={"photo": ("big.jpg", b"0" * (2 * 1024 * 1024 + 1), "image/jpeg")})
    assert big.status_code == 413


def test_invalid_category_rejected(client):
    headers = signup(client)
    r = client.post("/api/wardrobe", headers=headers, data={"category": "모자"})
    assert r.status_code == 422


def test_cannot_touch_other_users_items(client):
    alice = signup(client, email="alice@example.com")
    bob = signup(client, email="bob@example.com")
    item = add_item(client, alice)
    assert client.get("/api/wardrobe", headers=bob).json() == []
    assert client.patch(f"/api/wardrobe/{item['id']}", headers=bob, json={"color": "레드"}).status_code == 404
    assert client.delete(f"/api/wardrobe/{item['id']}", headers=bob).status_code == 404
    assert client.post("/api/wardrobe/wear", headers=bob, json={"item_ids": [item["id"]]}).status_code == 404


def test_wear_records_time(client):
    headers = signup(client)
    item = add_item(client, headers)
    r = client.post("/api/wardrobe/wear", headers=headers, json={"item_ids": [item["id"]]})
    assert r.status_code == 200 and r.json()[0]["last_worn_at"] is not None
