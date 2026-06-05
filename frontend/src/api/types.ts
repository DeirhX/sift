// Friendly aliases over the codegen'd OpenAPI schema. Import DTOs from here
// (not from ./schema directly) so the rest of the app reads cleanly and there's
// one place to adjust if the generator's shape changes.
//
// Regenerate the underlying schema with `npm run codegen` after backend changes.
import type { components } from './schema'

export type Schemas = components['schemas']

export type Face = Schemas['Face']
export type ImageItem = Schemas['ImageItem']
export type GroupedImageItem = Schemas['GroupedImageItem']
export type ImagesResponse = Schemas['ImagesResponse']
export type Group = Schemas['Group']
export type GroupsResponse = Schemas['GroupsResponse']
export type Scene = Schemas['Scene']
export type ScenesResponse = Schemas['ScenesResponse']
export type Location = Schemas['Location']
export type LocationsResponse = Schemas['LocationsResponse']
export type MetaResponse = Schemas['MetaResponse']
export type ClusterFacet = Schemas['ClusterFacet']
export type TagFacet = Schemas['TagFacet']
export type FolderFacet = Schemas['FolderFacet']
export type Ranges = Schemas['Ranges']
export type Counts = Schemas['Counts']
export type RootsResponse = Schemas['RootsResponse']
export type FsCompleteResponse = Schemas['FsCompleteResponse']
export type AnalyzeStatus = Schemas['AnalyzeStatus']
export type TaskSnapshot = Schemas['TaskSnapshot']
export type TaskListResponse = Schemas['TaskListResponse']
export type ApplyStatusResponse = Schemas['ApplyStatusResponse']
export type ApplyResponse = Schemas['ApplyResponse']
export type UndoResponse = Schemas['UndoResponse']
export type TrashStatusResponse = Schemas['TrashStatusResponse']
export type TrashListResponse = Schemas['TrashListResponse']
export type AutocullResponse = Schemas['AutocullResponse']
export type MergeResponse = Schemas['MergeResponse']
export type AssignFaceResponse = Schemas['AssignFaceResponse']
export type OkResponse = Schemas['OkResponse']
