/**
 * Confirmation explicite des actions sensibles (déconnexion ou révocation d'un
 * agent, rotation de la clé d'enregistrement, bascule OFFLINE, annulation d'une
 * tâche). La fenêtre reste ouverte en cas d'erreur et affiche le message du
 * backend.
 */

import type { ReactNode } from 'react';
import { Modal } from './Modal';

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  details?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onClose: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  details,
  confirmLabel = 'Confirmer',
  cancelLabel = 'Annuler',
  danger = false,
  busy = false,
  error = null,
  onConfirm,
  onClose,
}: ConfirmDialogProps): React.ReactElement {
  return (
    <Modal
      open={open}
      title={title}
      size="sm"
      closeOnEscape={!busy}
      onClose={() => {
        if (!busy) onClose();
      }}
      footer={
        <>
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={danger ? 'btn btn--danger' : 'btn btn--primary'}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? 'Traitement…' : confirmLabel}
          </button>
        </>
      }
    >
      <p className="dialog__message">{message}</p>
      {details ? <div className="dialog__details">{details}</div> : null}
      {error ? (
        <p className="alert alert--error" role="alert">
          {error}
        </p>
      ) : null}
    </Modal>
  );
}
