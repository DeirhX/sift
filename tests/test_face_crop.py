from io import BytesIO

from PIL import Image

from conftest import items_by_name


def test_face_crop_uses_face_from_requested_photo(tmp_path, make_env):
    path = tmp_path / "portrait.jpg"
    image = Image.new("RGB", (100, 80), "blue")
    box = [10, 20, 30, 40]
    image.paste("red", box)
    exif = Image.Exif()
    exif[274] = 6
    image.save(path, exif=exif)
    env = make_env({"folder": str(tmp_path), "images": [
        {"path": str(path), "filename": path.name, "imgw": 100, "imgh": 80,
         "faces": [{"bbox": box, "prob": 0.99, "cluster_id": 0}]},
        {"path": "/fake/other.jpg", "filename": "other.jpg", "faces": []},
    ]})
    photos = items_by_name(env.client)
    photo = photos[path.name]
    face = photo["faces"][0]["id"]
    response = env.client.get(f"/img/{photo['id']}", params={"face_id": face})
    assert response.status_code == 200
    with Image.open(BytesIO(response.content)) as crop:
        assert crop.size == (52, 46)
        red, _, blue = crop.getpixel((26, 20))
        assert red > blue
    other = photos["other.jpg"]["id"]
    assert env.client.get(f"/img/{other}", params={"face_id": face}).status_code == 404
