/**
 * Pagination des listes (agents, tâches, journaux).
 * La taille de page est imposée par la requête ; les bornes affichées sont
 * calculées à partir de `total` et `page_size` renvoyés par le backend.
 */

export interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  disabled?: boolean;
  /** Nom au pluriel de la ressource paginée (ex. « agents »). */
  itemLabel?: string;
}

function buildPageWindow(page: number, totalPages: number): number[] {
  const windowSize = 5;
  let start = Math.max(1, page - Math.floor(windowSize / 2));
  const end = Math.min(totalPages, start + windowSize - 1);
  start = Math.max(1, end - windowSize + 1);

  const pages: number[] = [];
  for (let current = start; current <= end; current += 1) pages.push(current);
  return pages;
}

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  disabled = false,
  itemLabel = 'éléments',
}: PaginationProps): React.ReactElement {
  const totalPages = pageSize > 0 ? Math.max(1, Math.ceil(total / pageSize)) : 1;
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);
  const pages = buildPageWindow(page, totalPages);

  return (
    <nav className="pagination" aria-label="Pagination">
      <p className="pagination__summary">
        {total === 0
          ? `Aucun ${itemLabel}`
          : `${first}–${last} sur ${total} ${itemLabel} — page ${page} sur ${totalPages}`}
      </p>
      <div className="pagination__controls">
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => onPageChange(page - 1)}
          disabled={disabled || page <= 1}
        >
          Précédent
        </button>
        {pages.map((value) => (
          <button
            key={value}
            type="button"
            className={value === page ? 'btn btn--primary btn--sm' : 'btn btn--ghost btn--sm'}
            aria-current={value === page ? 'page' : undefined}
            onClick={() => onPageChange(value)}
            disabled={disabled}
          >
            <span className="sr-only">Page </span>
            {value}
          </button>
        ))}
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => onPageChange(page + 1)}
          disabled={disabled || page >= totalPages}
        >
          Suivant
        </button>
      </div>
    </nav>
  );
}
