import { ArrowDownNarrowWide, ArrowUpNarrowWide } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  countPill,
  rowDelay,
  sortChoices,
  sortId,
  sortRows,
  sourceNumber,
  type NumberedEvidence,
  type SortChoice,
  type SortState,
} from "../../lib/answerTable";
import { ENTER, GLIDE, usePrefersReducedMotion } from "../../lib/motion";
import type { AnswerTable as AnswerTableData, Cell, Column, Row } from "../../types/AnswerEnvelope";

interface AnswerTableProps {
  table: AnswerTableData;
  sort: SortState;
  onSort: (sort: SortState) => void;
  numbered: NumberedEvidence[];
  /** A Source chip was pressed: the reading's number and the row it belongs to. */
  onSource: (n: number, row: Row) => void;
  /** The two footer lines (counts, then company and moment), already worded. */
  scope: { counts: string; source: string } | null;
  /** Stored in an earlier session: the rows are simply there. */
  reopened?: boolean;
}

/** The sort button and its menu: one pill in the card's header, a small floating list under it. */
function SortMenu({ choices, sort, onSort }: { choices: SortChoice[]; sort: SortState; onSort: (sort: SortState) => void }) {
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const button = useRef<HTMLButtonElement>(null);
  const root = useRef<HTMLSpanElement>(null);
  const items = useRef<Array<HTMLButtonElement | null>>([]);
  const current = choices.find((choice) => choice.id === sortId(sort)) ?? choices[0];
  const Arrow = current?.direction === "desc" ? ArrowUpNarrowWide : ArrowDownNarrowWide;

  useEffect(() => {
    if (!open) return;
    const at = Math.max(0, choices.findIndex((choice) => choice.id === current?.id));
    items.current[at]?.focus();
    const away = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
    // Focus moves once, when the menu opens.
  }, [open]);

  if (!current || choices.length < 2) return null;

  function close(refocus: boolean) {
    setOpen(false);
    if (refocus) button.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent) {
    const here = items.current.findIndex((item) => item === document.activeElement);
    const go = (to: number) => {
      event.preventDefault();
      items.current[(to + choices.length) % choices.length]?.focus();
    };
    if (event.key === "ArrowDown") go(here + 1);
    else if (event.key === "ArrowUp") go(here - 1);
    else if (event.key === "Home") go(0);
    else if (event.key === "End") go(choices.length - 1);
    else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    } else if (event.key === "Tab") close(false);
  }

  return (
    <span className="sortmenu" ref={root}>
      <button
        ref={button}
        type="button"
        className="sortmenu__button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Sort by: ${current.label}`}
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden="true">{current.label}</span>
        <Arrow size={15} strokeWidth={1.75} aria-hidden="true" />
      </button>
      {open ? (
        <div className="sortmenu__list" id={menuId} role="menu" aria-label="Sort order" onKeyDown={onKeyDown}>
          {choices.map((choice, index) => (
            <button
              key={choice.id}
              ref={(el) => {
                items.current[index] = el;
              }}
              type="button"
              role="menuitemradio"
              aria-checked={choice.id === current.id}
              tabIndex={-1}
              className="sortmenu__item"
              onClick={() => {
                onSort({ key: choice.key, direction: choice.direction });
                close(true);
              }}
            >
              {choice.label}
            </button>
          ))}
        </div>
      ) : null}
    </span>
  );
}

/** A cell's value in the voice of its column: mono for what is measured, a pill for a state, plain words otherwise. */
function CellValue({ column, cell }: { column: Column; cell: Cell | undefined }) {
  if (!cell) {
    return (
      <span className="atable__none">
        <span aria-hidden="true">–</span>
        <span className="sr-only">No value</span>
      </span>
    );
  }
  if (column.kind === "pill") {
    return (
      <span className="apill" data-tone={cell.tone ?? "neutral"}>
        {cell.display}
      </span>
    );
  }
  const measured = ["version", "percent", "number", "date"].includes(column.kind);
  const word = cell.tone === "attention" ? "needs attention" : cell.tone === "ok" ? "fine" : null;
  return (
    <>
      {word ? <span className="atable__tonedot" aria-hidden="true" /> : null}
      <span className={measured ? "atable__val mono" : "atable__val"}>{cell.display}</span>
      {word ? <span className="sr-only"> ({word})</span> : null}
    </>
  );
}

const ariaSort = (sort: SortState) => (sort.direction === "asc" ? "ascending" : "descending");

/** The list answer as a card: title and count, a sort menu, a real table, then the scope line. */
export function AnswerTable({ table, sort, onSort, numbered, onSource, scope, reopened = false }: AnswerTableProps) {
  const reduce = usePrefersReducedMotion();
  const titleId = useId();
  const choices = useMemo(() => sortChoices(table), [table]);
  const rows = useMemo(() => sortRows(table, sort), [table, sort]);
  const device = table.columns.find((column) => column.kind === "device") ?? table.columns[0];
  const others = table.columns.filter((column) => column !== device);
  // The first arrival of a table is the only time its rows come in one by one.
  const [arrived] = useState(() => !reopened && !reduce);

  return (
    <div className="atable">
      <table className="atable__table" aria-labelledby={titleId}>
        <caption className="atable__caption">
          <span className="atable__title" id={titleId}>
            {table.title}
          </span>
          <span className="atable__count">{countPill(table)}</span>
          <SortMenu choices={choices} sort={sort} onSort={onSort} />
        </caption>
        <thead>
          <tr>
            <th scope="col" data-kind={device.kind} aria-sort={sort.key === device.key ? ariaSort(sort) : undefined}>
              {device.label}
            </th>
            {others.map((column) => (
              <th
                key={column.key}
                scope="col"
                data-kind={column.kind}
                aria-sort={sort.key === column.key ? ariaSort(sort) : undefined}
              >
                {column.label}
              </th>
            ))}
            <th scope="col" data-kind="source">
              Source
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const delay = arrived ? rowDelay(index, reduce, reopened) : null;
            const n = sourceNumber(row, numbered);
            return (
              <motion.tr
                key={row.device_id + row.refs.join("|")}
                layout={reduce ? false : "position"}
                initial={delay === null ? false : { opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={delay === null ? GLIDE : { ...ENTER, delay }}
              >
                <th scope="row" className="atable__device">
                  <span className="atable__name">{row.label}</span>
                  <span className="atable__serial mono">{row.device_id}</span>
                </th>
                {others.map((column) => {
                  const cell = row.cells[column.key];
                  return (
                    <td key={column.key} data-kind={column.kind} data-tone={cell?.tone ?? undefined} data-label={column.label}>
                      <CellValue column={column} cell={cell} />
                    </td>
                  );
                })}
                <td data-kind="source" data-label="Source">
                  {n === null ? null : (
                    <button
                      type="button"
                      className="srcchip"
                      aria-label={`Source ${n} for ${row.label}`}
                      onClick={() => onSource(n, row)}
                    >
                      {n}
                    </button>
                  )}
                </td>
              </motion.tr>
            );
          })}
        </tbody>
      </table>
      {scope ? (
        <div className="atable__scope">
          <p>{scope.counts}</p>
          <p>{scope.source}</p>
        </div>
      ) : null}
    </div>
  );
}
