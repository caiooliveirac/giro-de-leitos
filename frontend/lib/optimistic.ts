'use client';

import {
  useMutation,
  useQueryClient,
  type QueryKey,
  type UseMutationOptions,
  type UseMutationResult,
} from '@tanstack/react-query';
import { useToastStore } from '@/lib/toast';
import { enqueue } from '@/lib/offline-queue';
import { ApiError } from '@/lib/api';

export interface OptimisticMutationConfig<TInput, TResource, TContext = unknown> {
  queryKey: QueryKey;
  mutationFn: (input: TInput) => Promise<TResource>;
  applyOptimistic?: (input: TInput, previous: unknown) => { next: unknown; context?: TContext };
  rollback?: (input: TInput, previous: unknown, ctx: TContext | undefined) => void;
  offlineRequest?: (input: TInput) => {
    url: string;
    method: string;
    headers?: Record<string, string>;
    body?: unknown;
  };
}

interface InternalContext<TContext> {
  previous: unknown;
  custom?: TContext;
}

export function createOptimisticMutation<TInput, TResource, TContext = unknown>(
  cfg: OptimisticMutationConfig<TInput, TResource, TContext>,
): () => UseMutationResult<TResource, unknown, TInput, InternalContext<TContext>> {
  return function useOptimisticMutation() {
    const queryClient = useQueryClient();
    const pushToast = useToastStore((s) => s.push);

    const options: UseMutationOptions<TResource, unknown, TInput, InternalContext<TContext>> = {
      mutationFn: cfg.mutationFn,
      onMutate: async (input) => {
        await queryClient.cancelQueries({ queryKey: cfg.queryKey });
        const previous = queryClient.getQueryData(cfg.queryKey);
        let custom: TContext | undefined;
        if (cfg.applyOptimistic) {
          const result = cfg.applyOptimistic(input, previous);
          queryClient.setQueryData(cfg.queryKey, result.next);
          custom = result.context;
        }
        return { previous, custom };
      },
      onError: async (err, input, context) => {
        if (context) {
          queryClient.setQueryData(cfg.queryKey, context.previous);
          cfg.rollback?.(input, context.previous, context.custom);
        }
        const isNetwork =
          !(err instanceof ApiError) ||
          (err instanceof ApiError && (err.status === 0 || err.status >= 500));
        if (isNetwork) {
          pushToast('Sem conexão — tentando de novo', 'warning');
          if (cfg.offlineRequest) {
            try {
              const req = cfg.offlineRequest(input);
              await enqueue({
                url: req.url,
                method: req.method,
                headers: {
                  'Content-Type': 'application/json',
                  ...(req.headers ?? {}),
                },
                body: req.body === undefined ? null : JSON.stringify(req.body),
              });
            } catch {
              // ignore
            }
          }
        } else if (err instanceof ApiError) {
          pushToast(err.message || 'Falha ao salvar', 'error');
        }
      },
      onSettled: () => {
        queryClient.invalidateQueries({ queryKey: cfg.queryKey });
      },
    };

    return useMutation<TResource, unknown, TInput, InternalContext<TContext>>(options);
  };
}
