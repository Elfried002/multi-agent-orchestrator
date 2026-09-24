/**
 * Tableau de données accessible : en-têtes `th` avec `scope="col"`, légende
 * réservée aux lecteurs d'écran, états de chargement et état vide explicites.
 * Les lignes ne sont pas cliquables : l'action est portée par un vrai bouton
 * dans une cellule, afin de rester utilisable au clavier.
 */

import type { ReactNode } from 'react';
import { EmptyState } from './EmptyState';
import { LoadingState } from './LoadingState';

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  className?: string;
  width?: string;
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  caption: string;
  emptyTitle: string;
  emptyDescription?: string;
  loading?: boolean;
  loadingLabel?: string;
  /** Actualisation en cours alors que des lignes sont déjà affichées. */
  refreshing?: boolean;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  emptyTitle,
  emptyDescription,
  loading = false,
  loadingLabel,
  refreshing = false,
}: DataTableProps<T>): React.ReactElement {
  if (loading && rows.length === 0) {
    return <LoadingState label={loadingLabel ?? 'Chargement des données…'} />;
  }

  if (rows.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />;
  }

  return (
    <div className="table-wrap" aria-busy={refreshing}>
      <table className="table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={column.className}
                style={column.width ? { width: column.width } : undefined}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((column) => (
                <td key={column.key} className={column.className}>
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
