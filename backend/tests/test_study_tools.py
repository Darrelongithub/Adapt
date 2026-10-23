"""F04: notes and flashcards are owned, validated and persistent."""


def test_notes_and_cards_persist_and_export(client, learner):
    note = client.post(
        "/api/v1/study/items", json={"kind": "note", "text": "  Remember the independent variable.  "}, headers=learner["headers"]
    )
    assert note.status_code == 200, note.text
    assert note.json()["item"]["text"] == "Remember the independent variable."
    card = client.post(
        "/api/v1/study/items",
        json={"kind": "card", "text": "What does a control group show?", "answer": "The comparison without the changed variable."},
        headers=learner["headers"],
    )
    assert card.status_code == 200, card.text

    listed = client.get("/api/v1/study/items")
    assert listed.status_code == 200
    assert [row["text"] for row in listed.json()["notes"]] == ["Remember the independent variable."]
    assert listed.json()["cards"][0]["answer"] == "The comparison without the changed variable."
    exported = client.get("/api/v1/me/export").json()["records"]["study_items"]
    assert {row["kind"] for row in exported} == {"note", "card"}
    assert any(row["answer"] == "The comparison without the changed variable." for row in exported)

    deleted = client.delete(f"/api/v1/study/items/{note.json()['item']['id']}", headers=learner["headers"])
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert client.get("/api/v1/study/items").json()["notes"] == []
    assert len(client.get("/api/v1/study/items").json()["cards"]) == 1


def test_study_items_reject_blank_or_mismatched_input(client, learner):
    for payload in (
        {"kind": "note", "text": "   "},
        {"kind": "card", "text": "Front", "answer": "  "},
        {"kind": "note", "text": "Note", "answer": "Do not discard this"},
    ):
        response = client.post("/api/v1/study/items", json=payload, headers=learner["headers"])
        assert response.status_code == 422
    assert client.get("/api/v1/study/items").json() == {"notes": [], "cards": []}


def test_study_items_cannot_be_read_or_deleted_by_another_learner(client, learner):
    created = client.post("/api/v1/study/items", json={"kind": "note", "text": "Private note"}, headers=learner["headers"])
    item_id = created.json()["item"]["id"]

    other = client.post("/api/v1/students", json={"as_guest": True, "display_name": "Other learner"})
    other_headers = {"x-csrf-token": other.json()["csrf_token"]}
    assert client.get("/api/v1/study/items").json() == {"notes": [], "cards": []}
    assert client.delete(f"/api/v1/study/items/{item_id}", headers=other_headers).status_code == 404
    client.cookies.clear()
    assert client.get("/api/v1/study/items").status_code == 401
