import type { ReactNode } from 'react';

export interface DetailItem {
  label: string;
  value: ReactNode;
}

/** Liste de définitions (dt/dd) pour les fiches détaillées. */
export function DetailList({ items }: { items: DetailItem[] }): React.ReactElement {
  return (
    <dl className="details">
      {items.map((item) => (
        <div className="details__row" key={item.label}>
          <dt className="details__label">{item.label}</dt>
          <dd className="details__value">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Bloc de texte brut (résultat de tâche, métadonnées JSON). */
export function CodeBlock({ content }: { content: string }): React.ReactElement {
  return <pre className="code-block">{content}</pre>;
}
