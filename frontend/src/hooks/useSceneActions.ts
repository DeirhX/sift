import { useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { regroupScenes, mergeScenes, splitScene, unmergeScene } from '../api'
import { invalidateRoots, PHOTO_DATA_QUERY_ROOTS } from '../queryKeys'
import { toast } from '../toast'

// Everything that reshapes scenes. Each action calls the server, reports a
// failure as a toast, then refetches the scene list and the scene count (meta).
// merge / unmerge / split rethrow so the caller can keep its selection or
// overlay open for a retry; regroup (a slider) does not.
export function useSceneActions(closeOverlay: () => void) {
  const qc = useQueryClient()

  const refetchScenes = useCallback(() => {
    qc.invalidateQueries({ queryKey: ['scenes'] })
    qc.invalidateQueries({ queryKey: ['meta'] })
  }, [qc])

  // Scene granularity knob: re-segment scenes by capture-time gap.
  const regroup = useCallback(async (gap: number) => {
    try {
      await regroupScenes(gap)
    } catch (error) {
      toast.failure('Couldn’t regroup scenes.', error)
    }
    refetchScenes()
  }, [refetchScenes])

  // Pin several scenes into one.
  const merge = useCallback(async (sceneGroups: number[]) => {
    try {
      await mergeScenes(sceneGroups)
    } catch (error) {
      toast.failure('Couldn’t merge those scenes.', error)
      throw error
    }
    refetchScenes()
  }, [refetchScenes])

  // Release a manually merged scene back to automatic segmentation.
  const unmerge = useCallback(async (sceneGroup: number) => {
    try {
      await unmergeScene(sceneGroup)
    } catch (error) {
      toast.failure('Couldn’t unmerge that scene.', error)
      throw error
    }
    refetchScenes()
  }, [refetchScenes])

  // Cut a scene in two at a photo; the open scene no longer exists as it was,
  // so its overlay closes.
  const split = useCallback(async (sceneGroup: number, imageId: number, hash: string) => {
    await splitScene(sceneGroup, imageId, hash)
    closeOverlay()
    invalidateRoots(qc, PHOTO_DATA_QUERY_ROOTS)
  }, [closeOverlay, qc])

  return { regroup, merge, unmerge, split }
}
