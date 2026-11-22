import { useCallback, useId, useState } from "react";

export interface FilterField {
  name: string;
  label: string;
  /** A closed set of choices; without it the field is free text. */
  options?: { value: string; label: string }[];
  allLabel?: string;
}

type Values = Record<string, string>;

function trimmed(values: Values): Values {
  return Object.fromEntries(
    Object.entries(values)
      .map(([key, value]) => [key, value.trim()] as const)
      .filter(([, value]) => value !== ""),
  );
}

/** What is typed (`draft`) is applied on submit (`applied`); both hold only the named fields. */
export function useAuditFilters(initial: Values = {}) {
  const [draft, setDraft] = useState<Values>(initial);
  const [applied, setApplied] = useState<Values>(initial);
  const change = useCallback((name: string, value: string) => setDraft((prev) => ({ ...prev, [name]: value })), []);
  const apply = useCallback(() => {
    setDraft((prev) => trimmed(prev));
    setApplied(trimmed(draft));
  }, [draft]);
  /** Sets one filter and applies it at once, as when a proposal id is chosen from a row. */
  const choose = useCallback((name: string, value: string) => {
    setDraft((prev) => ({ ...prev, [name]: value }));
    setApplied((prev) => ({ ...prev, [name]: value }));
  }, []);
  const clear = useCallback(() => {
    setDraft({});
    setApplied({});
  }, []);
  const active = Object.keys(applied).length > 0;
  return { draft, applied, active, change, apply, choose, clear };
}

interface AuditFiltersProps {
  label: string;
  fields: FilterField[];
  draft: Values;
  canClear: boolean;
  onChange: (name: string, value: string) => void;
  onApply: () => void;
  onClear: () => void;
}

export function AuditFilters({ label, fields, draft, canClear, onChange, onApply, onClear }: AuditFiltersProps) {
  const base = useId();
  return (
    <form
      className="filters"
      role="search"
      aria-label={label}
      onSubmit={(event) => {
        event.preventDefault();
        onApply();
      }}
    >
      {fields.map((field) => {
        const id = `${base}-${field.name}`;
        return (
          <div className="filters__field" key={field.name}>
            <label htmlFor={id}>{field.label}</label>
            {field.options ? (
              <select
                id={id}
                className="filters__input"
                value={draft[field.name] ?? ""}
                onChange={(event) => onChange(field.name, event.target.value)}
              >
                <option value="">{field.allLabel ?? "All"}</option>
                {field.options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={id}
                className="filters__input"
                type="text"
                autoComplete="off"
                spellCheck={false}
                value={draft[field.name] ?? ""}
                onChange={(event) => onChange(field.name, event.target.value)}
              />
            )}
          </div>
        );
      })}
      <div className="filters__actions">
        <button type="submit" className="link-btn">
          Apply filters
        </button>
        <button type="button" className="link-btn" onClick={onClear} disabled={!canClear}>
          Clear filters
        </button>
      </div>
    </form>
  );
}
