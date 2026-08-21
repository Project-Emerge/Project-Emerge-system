export type NeighborLink = [string, string];

/**
 * Collapses the per-robot neighbor lists into undirected edges. Each robot publishes its
 * own view of the neighborhood, so a mutual link arrives twice and a link is kept even
 * when only one of the two robots reported it.
 */
export function neighborLinks(neighbors: Record<string, string[]>): NeighborLink[] {
  const seen = new Set<string>();
  const links: NeighborLink[] = [];
  for (const [id, peers] of Object.entries(neighbors)) {
    for (const peer of peers) {
      if (peer === id) continue;
      const link: NeighborLink = id < peer ? [id, peer] : [peer, id];
      const key = link.join("|");
      if (seen.has(key)) continue;
      seen.add(key);
      links.push(link);
    }
  }
  return links;
}
