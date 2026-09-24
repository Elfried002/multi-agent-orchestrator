/** État vide explicite (par exemple : « aucun agent enregistré »). */
export function EmptyState({
  title,
  description,
}: {
  title: string;
  description?: string;
}): React.ReactElement {
  return (
    <div className="state state--empty" role="status">
      <p className="state__title">{title}</p>
      {description ? <p className="state__hint">{description}</p> : null}
    </div>
  );
}
