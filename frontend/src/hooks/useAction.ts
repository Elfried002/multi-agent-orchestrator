/**
 * Exécution d'une action mutante (POST) avec indicateur d'occupation et
 * restitution du message d'erreur renvoyé par le backend.
 */

import { useCallback, useRef, useState } from 'react';
import { isAbortError } from '../services/api';
import { describeError } from '../utils/errors';

export interface ActionRunner<A extends unknown[]> {
  run: (...args: A) => Promise<boolean>;
  busy: boolean;
  error: string | null;
  reset: () => void;
}

export function useAction<A extends unknown[]>(
  action: (...args: A) => Promise<void>,
): ActionRunner<A> {
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const actionRef = useRef(action);

  actionRef.current = action;

  const run = useCallback(async (...args: A): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      await actionRef.current(...args);
      return true;
    } catch (caught: unknown) {
      if (!isAbortError(caught)) setError(describeError(caught));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  const reset = useCallback(() => setError(null), []);

  return { run, busy, error, reset };
}
