/**
 * Fenêtre modale accessible : rôle `dialog`, `aria-modal`, fermeture par
 * Échap ou clic sur l'arrière-plan, piégeage du focus dans la boîte de dialogue
 * et restitution du focus à l'élément déclencheur à la fermeture.
 */

import { useEffect, useId, useRef, type ReactNode } from 'react';

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Pile des modales ouvertes : seule la modale la plus récente réagit à Échap,
 * ce qui permet d'ouvrir une boîte de confirmation au-dessus d'une autre sans
 * que la touche ferme les deux.
 */
const modalStack: symbol[] = [];
let bodyLockCount = 0;

export interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: 'sm' | 'md' | 'lg';
  closeOnEscape?: boolean;
}

export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  size = 'md',
  closeOnEscape = true,
}: ModalProps): React.ReactElement | null {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return undefined;

    const modalId = Symbol('modal');
    modalStack.push(modalId);

    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const node = dialogRef.current;

    const initialFocusable = node?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
    if (initialFocusable) initialFocusable.focus();
    else node?.focus();

    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape' && closeOnEscape) {
        if (modalStack[modalStack.length - 1] !== modalId) return;
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab' || !node) return;
      if (modalStack[modalStack.length - 1] !== modalId) return;

      const focusables = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
        (element) => element.offsetParent !== null,
      );
      if (focusables.length === 0) return;

      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    bodyLockCount += 1;
    document.body.classList.add('modal-open');

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      const stackIndex = modalStack.indexOf(modalId);
      if (stackIndex >= 0) modalStack.splice(stackIndex, 1);
      bodyLockCount = Math.max(0, bodyLockCount - 1);
      if (bodyLockCount === 0) document.body.classList.remove('modal-open');
      previouslyFocused?.focus();
    };
  }, [open, onClose, closeOnEscape]);

  if (!open) return null;

  return (
    <div
      className="modal-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className={`modal modal--${size}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className="modal__header">
          <h2 className="modal__title" id={titleId}>
            {title}
          </h2>
          <button
            type="button"
            className="btn btn--icon"
            onClick={onClose}
            aria-label="Fermer la fenêtre"
            disabled={!closeOnEscape}
          >
            ✕
          </button>
        </header>
        <div className="modal__body">{children}</div>
        {footer ? <footer className="modal__footer">{footer}</footer> : null}
      </div>
    </div>
  );
}
