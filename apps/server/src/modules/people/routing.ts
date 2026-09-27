import { sql } from 'drizzle-orm';
import type { Tx } from '../../db/client.js';

export type Candidate = {
  id: string;
  name: string;
  level: number;
  open: number;
  capacity: number;
  availability: string;
  department: string;
};

/** Everyone cleared to resolve work for a department, with their live load. */
export async function candidates(tx: Tx, orgId: string, departmentId: string): Promise<Candidate[]> {
  const rows = await tx.execute<Candidate>(sql`
    select u.id, u.name, c.level, d.name as department,
           (m.base_load + (select count(*) from tickets t where t.org_id = ${orgId} and t.assignee_id = u.id
                             and t.status not in ('resolved','closed')))::int as open,
           m.capacity, coalesce(a.status, 'available') as availability
      from memberships m
      join users u on u.id = m.user_id
      join clearances c on c.org_id = m.org_id and c.user_id = u.id and c.department_id = ${departmentId}
      join departments d on d.id = c.department_id
      left join staff_availability a on a.org_id = m.org_id and a.user_id = u.id
     where m.org_id = ${orgId} and c.level >= 2`);
  return rows.rows;
}

/**
 * Router: closest skill match (highest clearance) among available people, then the lightest load.
 * The reason is written onto the ticket so staff can see why routing happened.
 */
export async function pickAssignee(
  tx: Tx,
  orgId: string,
  departmentId: string | null,
  bucket: string,
  exclude: string | null = null,
): Promise<{ id: string; name: string; reason: string } | null> {
  if (!departmentId) return null;
  const pool = (await candidates(tx, orgId, departmentId)).filter((c) => c.availability === 'available' && c.id !== exclude);
  if (!pool.length) return null;
  pool.sort((a, b) => a.open / a.capacity - b.open / b.capacity || b.level - a.level);
  const pick = pool[0]!;
  return {
    id: pick.id,
    name: pick.name,
    reason: `closest skill match for ${bucket.toLowerCase()}, lightest live load in ${pick.department}`,
  };
}
