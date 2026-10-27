import type {
  ViewpointCreate,
  ViewpointUpdate,
  ViewpointView,
} from "../../shared/api/generated/models";

export type ViewpointFields = {
  stance: NonNullable<ViewpointCreate["stance"]>;
  thesis: string;
  invalidation: string;
  review_on: string;
  review_notes: string;
  status: ViewpointView["status"];
  confirmed: boolean;
  mark_reviewed: boolean;
};
export function viewpointFields(view?: ViewpointView): ViewpointFields {
  return {
    stance: view?.stance ?? "watch",
    thesis: view?.thesis ?? "",
    invalidation: view?.invalidation ?? "",
    review_on: view?.review_on ?? "",
    review_notes: view?.review_notes ?? "",
    status: view?.status ?? "active",
    confirmed: false,
    mark_reviewed: false,
  };
}
export function viewpointWrite(values: ViewpointFields): Omit<ViewpointUpdate, "revision"> {
  const thesis = values.thesis.trim(),
    invalidation = values.invalidation.trim();
  if (!thesis || !invalidation) throw new Error("请手动填写非空的观点依据与失效条件。");
  return {
    stance: values.stance,
    thesis,
    invalidation,
    review_on: values.review_on || null,
    review_notes: values.review_notes.trim(),
    status: values.status,
    mark_reviewed: values.mark_reviewed,
  };
}
export function confirmedViewpoint(runId: string, values: ViewpointFields): ViewpointCreate {
  if (!values.confirmed) throw new Error("请确认这是你本人整理并认可的研究观点。");
  const payload = viewpointWrite(values);
  return {
    run_id: runId,
    confirmed: true,
    stance: payload.stance,
    thesis: payload.thesis,
    invalidation: payload.invalidation,
    review_on: payload.review_on,
    review_notes: payload.review_notes,
  };
}
