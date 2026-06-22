"""视频列表顺序：存储保持 API 顺序（新→旧），发送从列表末尾往前（先发最旧）。"""
from __future__ import annotations

SOURCE_SEARCH = "search"
SOURCE_COLLECTION = "collection"
SOURCE_MANUAL = "manual"


def sends_newest_last(*, source: str = SOURCE_SEARCH, search_sort: str = "recent") -> bool:
    """收藏 / 搜索「最新」：列表头为新、尾为旧，发送时从尾往头发。"""
    if source == SOURCE_COLLECTION:
        return True
    if source == SOURCE_SEARCH and search_sort == "recent":
        return True
    return False


def send_order_indices(
    count: int,
    *,
    source: str = SOURCE_SEARCH,
    search_sort: str = "recent",
) -> list[int]:
    if count <= 0:
        return []
    if sends_newest_last(source=source, search_sort=search_sort):
        return list(range(count - 1, -1, -1))
    return list(range(count))


def prev_send_index(indices: list[int], vi: int) -> int | None:
    try:
        pos = indices.index(vi)
    except ValueError:
        return None
    if pos <= 0:
        return None
    return indices[pos - 1]


def send_sequence_pos(indices: list[int], vi: int) -> int:
    try:
        return indices.index(vi) + 1
    except ValueError:
        return vi + 1
