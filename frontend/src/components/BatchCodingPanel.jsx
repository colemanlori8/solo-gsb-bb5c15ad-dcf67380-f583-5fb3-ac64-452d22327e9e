import { useMemo, useState } from 'react'
import { api } from '../api'

/**
 * 检索批量编码面板：
 * 输入字面关键词 → 后端返回当前文档全部命中（含相互重叠）的稳定半开区间与上下文；
 * 逐项勾选 / 全选并选择一个既有编码 → 单事务批量 keep 写入，
 * 完全相同的既有标注记为跳过；任一非法项整批不落库。
 */
export default function BatchCodingPanel({ documentId, codes, onBatchSubmit, onLocate }) {
  const [open, setOpen] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [results, setResults] = useState(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState(null)
  const [codeId, setCodeId] = useState('')
  const [checked, setChecked] = useState(() => new Set())
  const [submitting, setSubmitting] = useState(false)
  const [batchResult, setBatchResult] = useState(null)
  const [batchError, setBatchError] = useState(null)

  const hitKey = (h) => `${h.start}-${h.end}`

  // 当前所选编码下，与既有标注完全相同的命中不可重复提交
  const identicalKeys = useMemo(() => {
    const set = new Set()
    if (!results || codeId === '') return set
    for (const h of results.hits) {
      if (h.overlaps.some((o) => o.code_id === Number(codeId) && o.identical)) {
        set.add(hitKey(h))
      }
    }
    return set
  }, [results, codeId])

  const selectableKeys = useMemo(() => {
    if (!results) return []
    return results.hits.map(hitKey).filter((k) => !identicalKeys.has(k))
  }, [results, identicalKeys])

  const allSelected =
    selectableKeys.length > 0 && selectableKeys.every((k) => checked.has(k))

  async function handleSearch(ev) {
    ev?.preventDefault()
    const q = keyword.trim()
    if (!q) return
    setSearching(true)
    setSearchError(null)
    setBatchResult(null)
    setBatchError(null)
    try {
      const data = await api.searchDocument(documentId, q)
      setResults(data)
      // 默认全选；随当前编码禁用完全相同的命中
      const keys = data.hits
        .filter(
          (h) =>
            !(codeId !== '' && h.overlaps.some((o) => o.code_id === Number(codeId) && o.identical))
        )
        .map(hitKey)
      setChecked(new Set(keys))
    } catch (e) {
      setResults(null)
      setChecked(new Set())
      setSearchError(e.message || String(e))
    } finally {
      setSearching(false)
    }
  }

  function toggleHit(h) {
    const k = hitKey(h)
    if (identicalKeys.has(k)) return
    setChecked((cur) => {
      const next = new Set(cur)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  }

  function toggleAll() {
    setChecked((cur) => {
      if (selectableKeys.every((k) => cur.has(k))) return new Set()
      return new Set(selectableKeys)
    })
  }

  function changeCode(value) {
    setCodeId(value)
    setBatchResult(null)
    // 编码改变后，移除变成“完全相同”的勾选项
    if (value === '') {
      setChecked(new Set())
      return
    }
    setChecked((cur) => {
      const next = new Set(cur)
      for (const h of results?.hits ?? []) {
        if (h.overlaps.some((o) => o.code_id === Number(value) && o.identical)) {
          next.delete(hitKey(h))
        }
      }
      return next
    })
  }

  const selectedHits = useMemo(() => {
    if (!results) return []
    return results.hits.filter((h) => checked.has(hitKey(h)) && !identicalKeys.has(hitKey(h)))
  }, [results, checked, identicalKeys])

  async function handleBatchSubmit(ev) {
    ev.preventDefault()
    if (codeId === '' || selectedHits.length === 0) return
    setSubmitting(true)
    setBatchError(null)
    setBatchResult(null)
    try {
      const result = await onBatchSubmit({
        document_id: documentId,
        code_id: Number(codeId),
        ranges: selectedHits.map((h) => ({ start: h.start, end: h.end, keyword: results.keyword })),
      })
      setBatchResult(result)
      // 提交后刷新正文高亮与计数，再重新检索以更新“已有标注”状态
      await handleSearch()
    } catch (e) {
      setBatchError(e.message || String(e))
    } finally {
      setSubmitting(false)
    }
  }

  const oneLine = (s) => s.replace(/\s+/g, ' ')

  return (
    <section className={`batch-panel ${open ? 'open' : ''}`}>
      <div className="form-title batch-head" onClick={() => setOpen((v) => !v)}>
        <span>🔎 检索批量编码</span>
        <button type="button" className="link-btn" aria-expanded={open}>
          {open ? '收起' : '展开'}
        </button>
      </div>

      {open && (
        <div className="batch-body">
          <form className="batch-search" onSubmit={handleSearch}>
            <input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="输入字面关键词，如：焦虑"
              maxLength={200}
            />
            <button type="submit" disabled={searching || !keyword.trim()}>
              {searching ? '检索中…' : '检索'}
            </button>
          </form>
          <p className="hint">字面匹配、区分大小写；返回全文全部命中（含相互重叠的命中），不使用正则。</p>

          {searchError && <div className="batch-error">{searchError}</div>}

          {results && (
            <>
              <div className="batch-meta">
                {results.truncated ? (
                  <span>命中过多，仅展示前 {results.count} 条</span>
                ) : (
                  <span>共 {results.count} 处命中「{results.keyword}」</span>
                )}
              </div>

              {results.count > 0 && (
                <label className="field">
                  <span>批量应用的既有编码</span>
                  <select value={codeId} onChange={(e) => changeCode(e.target.value)}>
                    <option value="" disabled>
                      选择既有编码…
                    </option>
                    {codes.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              {results.count > 0 && (
                <>
                  <label className="batch-select-all">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      disabled={selectableKeys.length === 0}
                    />
                    <span>
                      全选（已选 {selectedHits.length}
                      {identicalKeys.size > 0 && `，${identicalKeys.size} 处与所选编码完全相同`}）
                    </span>
                  </label>

                  <ul className="batch-hit-list">
                    {results.hits.map((h) => {
                      const k = hitKey(h)
                      const identical = identicalKeys.has(k)
                      return (
                        <li key={k} className={identical ? 'identical' : ''}>
                          <label className="batch-hit-row">
                            <input
                              type="checkbox"
                              checked={checked.has(k)}
                              disabled={identical}
                              onChange={() => toggleHit(h)}
                            />
                            <span className="batch-hit-main">
                              <span className="batch-hit-text" title={oneLine(h.text)}>
                                {oneLine(h.context_before)}
                                <mark>{oneLine(h.text)}</mark>
                                {oneLine(h.context_after)}
                              </span>
                              <span className="batch-hit-meta">
                                <code>[{h.start}, {h.end})</code>
                                {h.overlaps.length > 0 && (
                                  <span className="batch-badges">
                                    {h.overlaps.map((o) => (
                                      <span
                                        key={o.id}
                                        className={`batch-badge ${o.identical ? 'identical' : ''}`}
                                        title={
                                          o.identical
                                            ? `已有完全相同的${o.code_name}标注`
                                            : `与${o.code_name}标注重叠`
                                        }
                                      >
                                        <span className="dot" style={{ background: o.color }} />
                                        {o.code_name}
                                        {o.identical && '·已有'}
                                      </span>
                                    ))}
                                  </span>
                                )}
                              </span>
                            </span>
                          </label>
                          <button
                            type="button"
                            className="link-btn"
                            onClick={() => onLocate({ start: h.start, end: h.end })}
                          >
                            定位
                          </button>
                        </li>
                      )
                    })}
                  </ul>

                  <button
                    type="button"
                    className="primary-btn"
                    disabled={submitting || codeId === '' || selectedHits.length === 0}
                    onClick={handleBatchSubmit}
                  >
                    {submitting
                      ? '提交中…'
                      : `批量编码（${selectedHits.length} 项，保留边界 keep）`}
                  </button>
                  <p className="hint">
                    单事务提交：与所选编码完全相同的既有标注自动跳过；任一项校验失败则整批不落库。
                  </p>
                </>
              )}

              {results.count === 0 && <div className="batch-empty">未找到命中</div>}

              {batchResult && (
                <div className="batch-result">
                  新增 {batchResult.created_count} 条，跳过 {batchResult.skipped_count} 条
                  {batchResult.skipped_count > 0 && (
                    <span className="muted">
                      （完全相同的既有标注
                      {
                        batchResult.skipped.filter((s) => s.reason === 'identical_existing').length
                      }{' '}
                      条
                      {batchResult.skipped.some((s) => s.reason === 'duplicate_in_batch') &&
                        `、批次内重复 ${
                          batchResult.skipped.filter((s) => s.reason === 'duplicate_in_batch').length
                        } 条`}
                      ）
                    </span>
                  )}
                </div>
              )}
              {batchError && <div className="batch-error">{batchError}</div>}
            </>
          )}

          {codes.length === 0 && (
            <p className="hint">当前还没有任何编码，请先用下方表单新建一个既有编码后再批量提交。</p>
          )}
        </div>
      )}
    </section>
  )
}
