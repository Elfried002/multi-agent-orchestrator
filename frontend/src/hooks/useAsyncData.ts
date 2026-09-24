/**
 * Chargement de données asynchrone : état `loading` / `error` / `data`,
 * annulation des requêtes obsolètes (changement de filtre, démontage) et
 * possibilité de recharger manuellement.
 *
 * `key` doit décrire de manière unique la ressource demandée : tout changement
 * de filtre, de page ou de compteur de rafraîchissement déclenche un nouvel appel.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { isAbortError } from '../services/api';
import { describeError } from '../utils/errors';

export interface AsyncData<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
  setData: React.Dispatch<React.SetStateAction<T | null>>;
}

export function useAsyncData<T>(
  key: string,
  fetcher: (signal: AbortSignal) => Promise<T>,
): AsyncData<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState<number>(0);

  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    setLoading(true);
    setError(null);

    fetcherRef
      .current(controller.signal)
      .then((result) => {
        if (!active) return;
        setData(result);
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (!active || isAbortError(caught)) return;
        setError(describeError(caught));
        setLoading(false);
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  return { data, loading, error, reload, setData };
}
