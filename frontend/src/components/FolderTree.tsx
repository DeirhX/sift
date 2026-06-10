import { useMemo, useState } from 'react'
import type { Filters } from '../urlState'
import type { FolderFacet } from '../api/types'
import type { UpdateFilter } from '../types'

// Build a folder tree from the flat [{path, count}] facet the backend sends.
// Paths are split on either separator; each node keeps its real reconstructed
// path (so the backend prefix match works) and a direct image count. Totals are
// summed over each subtree, and single-child chains with no images of their own
// are compressed into one node (file-explorer style: "E:\F\Photos" not E:→F→Photos).

interface RawNode {
  children: Map<string, RawNode>
  direct: number
  path: string | null
  seg: string
  sep: string
  total: number
}

interface DisplayNode {
  label: string
  path: string
  direct: number
  total: number
  children: DisplayNode[]
}

function buildTree(folders: FolderFacet[]): RawNode {
  const root: RawNode = { children: new Map(), direct: 0, path: null, seg: '', sep: '/', total: 0 }
  for (const { path, count } of folders) {
    const sep = path.includes('\\') ? '\\' : '/'
    const segs = path.split(/[\\/]/).filter(Boolean)
    let node = root
    let acc = ''
    for (const seg of segs) {
      acc = acc ? acc + sep + seg : seg
      let child = node.children.get(seg)
      if (!child) {
        child = { children: new Map(), direct: 0, path: acc, seg, sep, total: 0 }
        node.children.set(seg, child)
      }
      node = child
    }
    node.direct += count
  }
  return root
}

function totalOf(node: RawNode): number {
  let t = node.direct || 0
  for (const c of node.children.values()) t += totalOf(c)
  node.total = t
  return t
}

// Collapse single-child, image-less chains; return a sorted display node.
function toDisplay(node: RawNode): DisplayNode {
  let cur = node
  let label = node.seg
  while (cur.direct === 0 && cur.children.size === 1) {
    const only = [...cur.children.values()][0]
    label += cur.sep + only.seg
    cur = only
  }
  const children = [...cur.children.values()]
    .map(toDisplay)
    .sort((a, b) => a.label.localeCompare(b.label))
  return { label, path: cur.path ?? '', direct: cur.direct, total: cur.total, children }
}

interface NodeProps {
  node: DisplayNode
  depth: number
  selected: string
  expanded: Set<string>
  onToggle: (path: string) => void
  onPick: (path: string) => void
}

function Node({ node, depth, selected, expanded, onToggle, onPick }: NodeProps) {
  const open = expanded.has(node.path)
  const isSel = selected === node.path
  const hasKids = node.children.length > 0
  return (
    <li className="folder-node">
      <div className={'folder-row' + (isSel ? ' selected' : '')} style={{ paddingLeft: depth * 12 }}>
        <button
          className={'folder-twist' + (hasKids ? '' : ' leaf')}
          onClick={() => hasKids && onToggle(node.path)}
          tabIndex={hasKids ? 0 : -1}
        >{hasKids ? (open ? '▾' : '▸') : '·'}</button>
        <button className="folder-label" title={node.path} onClick={() => onPick(node.path)}>
          <span className="folder-name">{node.label}</span>
          <span className="folder-count">{node.total}</span>
        </button>
      </div>
      {hasKids && open && (
        <ul className="folder-children">
          {node.children.map((c) => (
            <Node key={c.path} node={c} depth={depth + 1}
              selected={selected} expanded={expanded} onToggle={onToggle} onPick={onPick} />
          ))}
        </ul>
      )}
    </li>
  )
}

interface FolderTreeProps {
  folders?: FolderFacet[]
  filters: Filters
  updateFilter: UpdateFilter
  // When rendered inside a collapsible Section the title is supplied by the
  // section header, so the component drops its own "Folders" label.
  embedded?: boolean
}

export default function FolderTree({ folders, filters, updateFilter, embedded = false }: FolderTreeProps) {
  const forest = useMemo<DisplayNode[]>(() => {
    if (!folders?.length) return []
    const root = buildTree(folders)
    totalOf(root)
    return [...root.children.values()].map(toDisplay)
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [folders])

  // Expand the chain down to the currently-selected folder by default.
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    const s = new Set<string>()
    if (filters.folder) {
      const sep = filters.folder.includes('\\') ? '\\' : '/'
      const segs = filters.folder.split(/[\\/]/).filter(Boolean)
      let acc = ''
      for (const seg of segs) { acc = acc ? acc + sep + seg : seg; s.add(acc) }
    }
    return s
  })

  if (!forest.length) return null

  const toggle = (path: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path); else next.add(path)
      return next
    })

  const pick = (path: string) =>
    updateFilter({ folder: filters.folder === path ? '' : path })

  return (
    <div className="filter-group">
      {embedded ? (
        filters.folder && (
          <div className="section-tools">
            <button className="link-btn" onClick={() => updateFilter({ folder: '' })}>clear</button>
          </div>
        )
      ) : (
        <label className="group-label">
          Folders
          {filters.folder && (
            <button className="link-btn" onClick={() => updateFilter({ folder: '' })}>clear</button>
          )}
        </label>
      )}

      <ul className="folder-tree">
        {forest.map((n) => (
          <Node key={n.path} node={n} depth={0}
            selected={filters.folder} expanded={expanded} onToggle={toggle} onPick={pick} />
        ))}
      </ul>

      {filters.folder && (
        <label className="folder-subtoggle" title="Off = only photos directly in this folder, not its subfolders">
          <input
            type="checkbox"
            checked={filters.folderRecursive}
            onChange={(e) => updateFilter({ folderRecursive: e.target.checked })}
          />
          Include subfolders
        </label>
      )}
    </div>
  )
}
