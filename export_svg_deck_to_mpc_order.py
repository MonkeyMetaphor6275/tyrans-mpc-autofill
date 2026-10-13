from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from xml.etree import ElementTree as ET


DEFAULT_STOCK = "(S30) Standard Smooth"
DEFAULT_FOIL = False
MAX_CARDS_PER_ORDER = 612
SUPPORTED_IMAGE_EXTENSIONS = (".svg", ".png", ".jpg", ".jpeg", ".webp")


def slugify(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", value.strip())
    return text.strip("_") or "card"


def normalize_row(row: dict[str, str]) -> dict[str, str]:
    return {(key or "").strip().lower(): (value or "").strip() for key, value in row.items()}


def get_first(row: dict[str, str], keys: list[str], default: str = "") -> str:
    for key in keys:
        value = row.get(key.lower(), "")
        if value:
            return value
    return default


def parse_count(value: str) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 1


def read_cards(csv_path: Path) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            normalized = normalize_row(row)
            name = get_first(normalized, ["nom", "name", "title"])
            if not name:
                continue
            cards.append(normalized)
    return cards


def build_card_entries(csv_path: Path, images_dir: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for index, row in enumerate(read_cards(csv_path), start=1):
        card_name = get_first(row, ["nom", "name", "title"])
        count = parse_count(get_first(row, ["Impression", "impression"], default="1"))
        if count <= 0:
            continue

        image_path = resolve_image_path(images_dir, card_name, index)
        entries.append({"name": card_name, "count": count, "image_path": image_path})

    return entries


def split_entries(entries: list[dict[str, object]], max_cards_per_order: int) -> list[list[tuple[dict[str, object], int]]]:
    if max_cards_per_order <= 0:
        raise ValueError("max_cards_per_order must be greater than zero")

    chunks: list[list[tuple[dict[str, object], int]]] = []
    current_chunk: list[tuple[dict[str, object], int]] = []
    current_quantity = 0

    for entry in entries:
        remaining = int(entry["count"])

        while remaining > 0:
            capacity = max_cards_per_order - current_quantity
            if capacity == 0:
                chunks.append(current_chunk)
                current_chunk = []
                current_quantity = 0
                capacity = max_cards_per_order

            take = min(remaining, capacity)
            current_chunk.append((entry, take))
            current_quantity += take
            remaining -= take

            if current_quantity == max_cards_per_order:
                chunks.append(current_chunk)
                current_chunk = []
                current_quantity = 0

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def resolve_image_path(images_dir: Path, card_name: str, index: int) -> Path:
    slug = slugify(card_name)
    expected_base_name = f"{index:03d}_{slug}"

    direct_candidate = images_dir / f"{expected_base_name}.png"
    if direct_candidate.exists():
        return direct_candidate

    for extension in SUPPORTED_IMAGE_EXTENSIONS:
        candidate = images_dir / f"{expected_base_name}{extension}"
        if candidate.exists():
            return candidate

    matches = []
    for extension in SUPPORTED_IMAGE_EXTENSIONS:
        matches.extend(sorted(images_dir.glob(f"{expected_base_name}*{extension}")))
    if matches:
        return matches[0]

    raise FileNotFoundError(f"Could not find an image for card '{card_name}' using base name '{expected_base_name}'")


def build_order_xml(
    chunk_entries: list[tuple[dict[str, object], int]],
    stock: str,
    foil: bool,
    back_image: Path,
) -> ET.Element:
    root = ET.Element("order")
    details = ET.SubElement(root, "details")
    total_quantity = 0

    front_cards = ET.SubElement(root, "fronts")

    for entry, count in chunk_entries:
        card_name = str(entry["name"])
        image_path = Path(str(entry["image_path"]))
        slot_start = total_quantity
        total_quantity += count

        card_element = ET.SubElement(front_cards, "card")
        ET.SubElement(card_element, "id").text = str(image_path.resolve())
        ET.SubElement(card_element, "sourceType").text = "Local File"
        ET.SubElement(card_element, "slots").text = ",".join(str(slot) for slot in range(slot_start, total_quantity))
        ET.SubElement(card_element, "name").text = image_path.name
        ET.SubElement(card_element, "query").text = card_name

    ET.SubElement(details, "quantity").text = str(total_quantity)
    ET.SubElement(details, "stock").text = stock
    ET.SubElement(details, "foil").text = "true" if foil else "false"

    ET.SubElement(root, "backs")
    cardback = ET.SubElement(root, "cardback")
    cardback.text = str(back_image.resolve())

    return root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an MPC Autofill XML order from Tyrans SVG output.")
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("Updated_Cards.csv"),
        help="CSV file that drove the Tyrans generation",
    )
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=Path("output"),
        help="Directory containing the generated Tyrans card images",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("mpc_order.xml"),
        help="Destination XML file for MPC Autofill",
    )
    parser.add_argument(
        "--stock",
        type=str,
        default=DEFAULT_STOCK,
        help="Cardstock value written into the MPC order",
    )
    parser.add_argument(
        "--foil",
        action="store_true",
        default=DEFAULT_FOIL,
        help="Mark the order as foil",
    )
    parser.add_argument(
        "--back-image",
        type=Path,
        required=True,
        help="Shared card-back image path. This is required for MPC Autofill orders.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_path = args.csv.resolve()
    images_dir = args.images_dir.resolve()
    output_path = args.output.resolve()

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file does not exist: {csv_path}")
    if not images_dir.exists():
        raise FileNotFoundError(f"Image directory does not exist: {images_dir}")

    back_image = args.back_image.resolve()
    if not back_image.exists():
        raise FileNotFoundError(f"Back image does not exist: {back_image}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    entries = build_card_entries(csv_path=csv_path, images_dir=images_dir)
    chunks = split_entries(entries=entries, max_cards_per_order=MAX_CARDS_PER_ORDER)

    if len(chunks) == 1:
        root = build_order_xml(
            chunk_entries=chunks[0],
            stock=args.stock,
            foil=args.foil,
            back_image=back_image,
        )
        ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
        print(f"Wrote MPC Autofill order to {output_path}")
        return

    output_files: list[Path] = []
    for i, chunk in enumerate(chunks, start=1):
        chunk_output_path = output_path.with_name(f"{output_path.stem}_{i:03d}{output_path.suffix}")
        root = build_order_xml(
            chunk_entries=chunk,
            stock=args.stock,
            foil=args.foil,
            back_image=back_image,
        )
        ET.ElementTree(root).write(chunk_output_path, encoding="utf-8", xml_declaration=True)
        output_files.append(chunk_output_path)

    print(f"Wrote {len(output_files)} MPC Autofill order files:")
    for file_path in output_files:
        print(f"- {file_path}")


if __name__ == "__main__":
    main()