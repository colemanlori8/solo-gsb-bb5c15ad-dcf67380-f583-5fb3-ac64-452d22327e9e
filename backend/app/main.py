"""质性分析工作台后端：FastAPI + SQLite。"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .conflicts import (
    find_conflicts,
    merge_same_code,
    split_containers,
    subtract_ranges,
)
from .cooccurrence import build_code_intervals, intersect_intervals
from .db import get_db, init_db, list_codings, next_color

Resolution = Literal["keep", "merge", "split"]


# ---------- Pydantic 模型 ----------

class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)


class CodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str | None = None


class CodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = None


class PreviewRequest(BaseModel):
    document_id: int
    code_id: int
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    exclude_id: int | None = None


class CodingCreate(BaseModel):
    document_id: int
    code_id: int
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    resolution: Resolution = "keep"


class CodingUpdate(BaseModel):
    code_id: int | None = None
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)
    resolution: Resolution = "keep"


# ---------- 应用 ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="访谈转录质性分析工作台", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_document_or_404(conn, document_id: int) -> dict:
    row = conn.execute(
        "SELECT id, title, content, created_at FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return dict(row)


def get_code_or_404(conn, code_id: int) -> dict:
    row = conn.execute(
        "SELECT id, name, color, created_at FROM codes WHERE id = ?", (code_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="编码不存在")
    return dict(row)


def validate_range(doc: dict, start: int, end: int) -> None:
    if end <= start:
        raise HTTPException(status_code=422, detail="区间不合法：end 必须大于 start")
    if end > len(doc["content"]):
        raise HTTPException(status_code=422, detail="区间超出文本长度")


# ---------- 健康检查 ----------

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ---------- 文档 ----------

@app.get("/api/documents")
def list_documents() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.title, d.created_at, length(d.content) AS length,
                   (SELECT COUNT(*) FROM codings cg WHERE cg.document_id = d.id) AS coding_count
            FROM documents d ORDER BY d.id
            """
        ).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/documents", status_code=201)
def create_document(body: DocumentCreate) -> dict:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO documents (title, content) VALUES (?, ?)",
            (body.title, body.content),
        )
        return get_document_or_404(conn, cur.lastrowid)


@app.get("/api/documents/{document_id}")
def get_document(document_id: int) -> dict:
    with get_db() as conn:
        doc = get_document_or_404(conn, document_id)
        codes = [
            dict(r)
            for r in conn.execute(
                "SELECT id, name, color, created_at FROM codes ORDER BY id"
            ).fetchall()
        ]
        return {
            "document": doc,
            "codes": codes,
            "codings": list_codings(conn, document_id),
        }


@app.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: int) -> Response:
    with get_db() as conn:
        get_document_or_404(conn, document_id)
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    return Response(status_code=204)


# ---------- 编码本（codebook） ----------

@app.get("/api/codes")
def list_codes() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.color, c.created_at,
                   (SELECT COUNT(*) FROM codings cg WHERE cg.code_id = c.id) AS usage_count
            FROM codes c ORDER BY c.id
            """
        ).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/codes", status_code=201)
def create_code(body: CodeCreate) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="编码名不能为空")
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM codes WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="同名编码已存在")
        color = body.color or next_color(conn)
        cur = conn.execute(
            "INSERT INTO codes (name, color) VALUES (?, ?)", (name, color)
        )
        return get_code_or_404(conn, cur.lastrowid)


@app.put("/api/codes/{code_id}")
def update_code(code_id: int, body: CodeUpdate) -> dict:
    with get_db() as conn:
        code = get_code_or_404(conn, code_id)
        name = (body.name or code["name"]).strip()
        color = body.color or code["color"]
        dup = conn.execute(
            "SELECT id FROM codes WHERE name = ? AND id != ?", (name, code_id)
        ).fetchone()
        if dup:
            raise HTTPException(status_code=409, detail="同名编码已存在")
        conn.execute(
            "UPDATE codes SET name = ?, color = ? WHERE id = ?",
            (name, color, code_id),
        )
        return get_code_or_404(conn, code_id)


@app.delete("/api/codes/{code_id}", status_code=204)
def delete_code(code_id: int) -> Response:
    with get_db() as conn:
        get_code_or_404(conn, code_id)
        conn.execute("DELETE FROM codes WHERE id = ?", (code_id,))
    return Response(status_code=204)


# ---------- 冲突预览 ----------

@app.post("/api/codings/preview")
def preview_coding(body: PreviewRequest) -> dict:
    with get_db() as conn:
        doc = get_document_or_404(conn, body.document_id)
        get_code_or_404(conn, body.code_id)
        validate_range(doc, body.start, body.end)
        existing = list_codings(conn, body.document_id)
        conflicts = find_conflicts(
            existing, body.code_id, body.start, body.end, body.exclude_id
        )
        return {
            "has_conflicts": len(conflicts) > 0,
            "conflicts": conflicts,
            "available_resolutions": sorted(
                {a for c in conflicts for a in c["actions"]} | {"keep"}
            ),
        }


# ---------- 编码的创建 / 修改 / 删除 ----------

INSERT_CODING = (
    'INSERT INTO codings (document_id, code_id, start, "end") VALUES (?, ?, ?, ?)'
)


def apply_coding(
    conn,
    document_id: int,
    code_id: int,
    start: int,
    end: int,
    resolution: Resolution,
    exclude_id: int | None = None,
) -> list[int]:
    """按决策写入编码，返回产生的 coding id 列表。"""
    existing = [
        c for c in list_codings(conn, document_id) if c["id"] != exclude_id
    ]
    conflicts = find_conflicts(existing, code_id, start, end)
    same = [c["coding"] for c in conflicts if c["type"] == "same_code"]
    containers = [c["coding"] for c in conflicts if c["type"] == "nested_container"]
    contained = [c["coding"] for c in conflicts if c["type"] == "nested_contained"]

    if resolution == "merge" and same:
        m_start, m_end, absorbed = merge_same_code(existing, code_id, start, end)
        for cid in absorbed:
            conn.execute("DELETE FROM codings WHERE id = ?", (cid,))
        cur = conn.execute(INSERT_CODING, (document_id, code_id, m_start, m_end))
        return [cur.lastrowid]

    if resolution == "split" and (containers or contained):
        for op in split_containers(containers, start, end):
            old = next(c for c in containers if c["id"] == op["id"])
            conn.execute("DELETE FROM codings WHERE id = ?", (op["id"],))
            for s, e in op["parts"]:
                conn.execute(
                    INSERT_CODING, (document_id, old["code_id"], s, e)
                )
        holes = [(c["start"], c["end"]) for c in contained]
        ids = []
        for s, e in subtract_ranges(start, end, holes):
            cur = conn.execute(INSERT_CODING, (document_id, code_id, s, e))
            ids.append(cur.lastrowid)
        return ids

    cur = conn.execute(INSERT_CODING, (document_id, code_id, start, end))
    return [cur.lastrowid]


@app.post("/api/codings", status_code=201)
def create_coding(body: CodingCreate) -> dict:
    with get_db() as conn:
        doc = get_document_or_404(conn, body.document_id)
        get_code_or_404(conn, body.code_id)
        validate_range(doc, body.start, body.end)
        ids = apply_coding(
            conn, body.document_id, body.code_id, body.start, body.end,
            body.resolution,
        )
        return {"ids": ids, "codings": list_codings(conn, body.document_id)}


@app.put("/api/codings/{coding_id}")
def update_coding(coding_id: int, body: CodingUpdate) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, document_id, code_id, start, \"end\" FROM codings WHERE id = ?",
            (coding_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="编码标注不存在")
        old = dict(row)
        doc = get_document_or_404(conn, old["document_id"])
        code_id = body.code_id if body.code_id is not None else old["code_id"]
        start = body.start if body.start is not None else old["start"]
        end = body.end if body.end is not None else old["end"]
        get_code_or_404(conn, code_id)
        validate_range(doc, start, end)
        if body.resolution == "keep":
            # 无结构变更：原地更新，保留原 id
            conn.execute(
                'UPDATE codings SET code_id = ?, start = ?, "end" = ?,'
                " updated_at = datetime('now') WHERE id = ?",
                (code_id, start, end, coding_id),
            )
            ids = [coding_id]
        else:
            # merge / split 会改动其它编码：删除旧标注后按决策重写（同事务，失败回滚）
            conn.execute("DELETE FROM codings WHERE id = ?", (coding_id,))
            ids = apply_coding(
                conn, old["document_id"], code_id, start, end,
                body.resolution, exclude_id=coding_id,
            )
        return {"ids": ids, "codings": list_codings(conn, old["document_id"])}


@app.delete("/api/codings/{coding_id}", status_code=204)
def delete_coding(coding_id: int) -> Response:
    with get_db() as conn:
        row = conn.execute("SELECT id FROM codings WHERE id = ?", (coding_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="编码标注不存在")
        conn.execute("DELETE FROM codings WHERE id = ?", (coding_id,))
    return Response(status_code=204)


# ---------- 导出 ----------

@app.get("/api/documents/{document_id}/export")
def export_document(document_id: int) -> JSONResponse:
    with get_db() as conn:
        doc = get_document_or_404(conn, document_id)
        codes = [
            dict(r)
            for r in conn.execute(
                "SELECT id, name, color FROM codes ORDER BY id"
            ).fetchall()
        ]
        codings = list_codings(conn, document_id)
    content = doc["content"]
    payload = {
        "document": {
            "id": doc["id"],
            "title": doc["title"],
            "content": content,
            "length": len(content),
        },
        "codes": codes,
        "codings": [
            {
                "id": c["id"],
                "code_id": c["code_id"],
                "code": c["code_name"],
                "color": c["code_color"],
                "start": c["start"],
                "end": c["end"],
                "text": content[c["start"] : c["end"]],
            }
            for c in codings
        ],
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
    filename = f"codings-doc-{document_id}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------- 编码共现统计 ----------

@app.get("/api/documents/{document_id}/cooccurrence")
def cooccurrence(
    document_id: int,
    codes: list[int] | None = Query(default=None),
) -> dict:
    """按当前文档返回编码两两共现矩阵数据。

    查询参数 `codes` 为可选的编码筛选（?codes=1&codes=2）；缺省统计编码本
    全部编码。每个编码先各自合并重叠/相邻区间，再对编码对求交集，保证
    共享字符不被重复计数。
    """
    with get_db() as conn:
        doc = get_document_or_404(conn, document_id)
        all_codes = {
            r["id"]: {"id": r["id"], "name": r["name"], "color": r["color"]}
            for r in conn.execute("SELECT id, name, color FROM codes ORDER BY id")
        }
        if codes:
            unknown = [cid for cid in dict.fromkeys(codes) if cid not in all_codes]
            if unknown:
                raise HTTPException(
                    status_code=404, detail=f"编码不存在: {unknown}"
                )
            member_ids = sorted(set(codes))
        else:
            member_ids = sorted(all_codes)
        codings = list_codings(conn, document_id)

    intervals = build_code_intervals(codings)
    content = doc["content"]
    pairs: list[dict] = []
    for i, ci in enumerate(member_ids):
        for cj in member_ids[i:]:
            ivs_i = intervals.get(ci, [])
            ivs_j = intervals.get(cj, [])
            shared = ivs_i if ci == cj else intersect_intervals(ivs_i, ivs_j)
            pairs.append(
                {
                    "code_a_id": ci,
                    "code_b_id": cj,
                    "diagonal": ci == cj,
                    "char_count": sum(e - s for s, e in shared),
                    "segment_count": len(shared),
                    "segments": [
                        {"start": s, "end": e, "text": content[s:e]}
                        for s, e in shared
                    ],
                }
            )
    return {
        "document_id": document_id,
        "codes": [all_codes[cid] for cid in member_ids],
        "pairs": pairs,
    }
