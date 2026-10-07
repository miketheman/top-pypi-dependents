// Every entry of the search index ranked past the page whose name contains the
// needle, in rank order. Entries are `[project, rank, ...]`.
export const pastPage = (index, needle, onPage) =>
  index.filter(([project, rank]) => rank > onPage && project.includes(needle));
