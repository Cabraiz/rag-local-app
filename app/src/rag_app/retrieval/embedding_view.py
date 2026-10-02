"""Read-only local embedding inspection, scoped to the canonical current corpus."""
import math
from . import catalog, neural_client, semantic_policy
from .corpus import QdrantAdapter, release_settings, vectors, require_profile
from .domain import RequestError

MAX_POINTS = 64


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def normalized(value, dimension):
    if (not isinstance(value, list) or len(value) != dimension
            or not all(type(x) in (int, float) and math.isfinite(x) for x in value)):
        raise RequestError('EMBEDDING_VIEW_INVALID_VECTOR', 503)
    norm = math.sqrt(dot(value, value))
    if norm < 1e-12 or not math.isfinite(norm):
        raise RequestError('EMBEDDING_VIEW_INVALID_VECTOR', 503)
    return [x / norm for x in value]


def pca(rows):
    """Three PCA axes via a bounded symmetric Gram-matrix Jacobi eigensolver.

    Centered unit embeddings: 3D Euclidean geometry approximates original
    Euclidean geometry; cosine rankings below always use the original vectors.
    """
    n, d = len(rows), len(rows[0])
    mean = [sum(row[j] for row in rows) / n for j in range(d)]
    centered = [[x - m for x, m in zip(row, mean)] for row in rows]
    gram = [[dot(a, b) for b in centered] for a in centered]
    eigen = [[float(i == j) for j in range(n)] for i in range(n)]
    for _ in range(32):
        largest = 0.0
        for p in range(n):
            for q in range(p + 1, n):
                off = gram[p][q]
                largest = max(largest, abs(off))
                if abs(off) < 1e-12:
                    continue
                tau = (gram[q][q] - gram[p][p]) / (2 * off)
                t = math.copysign(1.0, tau) / (abs(tau) + math.hypot(1, tau))
                c, s = 1 / math.sqrt(1 + t * t), t / math.sqrt(1 + t * t)
                pp, qq = gram[p][p], gram[q][q]
                gram[p][p], gram[q][q] = pp - t * off, qq + t * off
                gram[p][q] = gram[q][p] = 0.0
                for k in range(n):
                    if k not in (p, q):
                        kp, kq = gram[k][p], gram[k][q]
                        gram[k][p] = gram[p][k] = c * kp - s * kq
                        gram[k][q] = gram[q][k] = s * kp + c * kq
                    ep, eq = eigen[k][p], eigen[k][q]
                    eigen[k][p], eigen[k][q] = c * ep - s * eq, s * ep + c * eq
        if largest < 1e-10:
            break
    else:
        raise RequestError('EMBEDDING_PROJECTION_NOT_CONVERGED', 503)
    values = [max(0.0, gram[i][i]) for i in range(n)]
    order = sorted(range(n), key=lambda i: (-values[i], i))[:3]
    axes = []
    for i in order:
        axis = ([sum(eigen[k][i] * centered[k][j] for k in range(n)) / math.sqrt(values[i])
                 for j in range(d)] if values[i] > 1e-10 else [0.0] * d)
        pivot = max(range(d), key=lambda j: abs(axis[j]))
        if axis[pivot] < 0:
            axis = [-x for x in axis]
        axes.append(axis)
    axes.extend([[0.0] * d for _ in range(3 - len(axes))])
    total = sum(values)
    retained = min(1.0, sum(values[i] for i in order) / total) if total > 1e-10 else 0.0
    return mean, axes, retained, [values[i] / total if total > 1e-10 else 0.0 for i in order]


def project(vector, mean, axes):
    return [round(dot([x - m for x, m in zip(vector, mean)], axis), 7) for axis in axes]


def read(who, question=None, index=None):
    require_profile()
    initial = catalog.read(who)
    release = initial['release_id']
    if not release or not initial['documents']:
        return {'points': [], 'edges': [], 'query': None, 'release_id': release,
                'embedding_version': initial['embedding_version'], 'total_chunks': 0,
                'sampled': False, 'max_points': MAX_POINTS, 'projection': None,
                'source': 'Qdrant dense vectors', 'cloud_calls': 0}
    name, dimension, neural = release_settings(release)
    canonical = [dict(id=str(chunk['id']), document_id=doc['id'], title=doc['title'],
                      quote=chunk['quote'], source_key=doc['source_key'])
                 for doc in initial['documents'] for chunk in doc['chunks']]
    chosen = canonical[:MAX_POINTS]
    response = (index or QdrantAdapter()).call('POST', '/collections/' + name + '/points', {
        'ids': [p['id'] for p in chosen], 'with_payload': True, 'with_vector': ['dense']})
    try:
        result = response['result']
        if not isinstance(result, list) or len(result) != len(chosen):
            raise ValueError()
        by_id = {str(p['id']): p for p in result}
        if len(by_id) != len(chosen) or set(by_id) != {p['id'] for p in chosen}:
            raise ValueError()
        dense = []
        for item in chosen:
            point = by_id[item['id']]
            if not isinstance(point.get('payload'), dict) or not isinstance(point.get('vector'), dict):
                raise ValueError()
            expected = {'tenant': who.tenant, 'actor': who.actor,
                        'release_id': release, 'chunk_id': item['id']}
            if any(point['payload'].get(k) != v for k, v in expected.items()):
                raise ValueError()
            dense.append(normalized(point['vector']['dense'], dimension))
    except (KeyError, TypeError, ValueError):
        raise RequestError('EMBEDDING_VIEW_INDEX_MISMATCH', 503) from None
    mean, axes, retained, ratios = pca(dense)
    scores = [[max(-1.0, min(1.0, dot(a, b))) for b in dense] for a in dense]
    edges, pairs = [], set()
    for i, item in enumerate(chosen):
        neighbors = sorted((j for j in range(len(chosen)) if j != i), key=lambda j: (-scores[i][j], chosen[j]['id']))[:3]
        item.update(position=project(dense[i], mean, axes), vector_preview=[round(x, 6) for x in dense[i][:12]],
                    neighbors=[{'id': chosen[j]['id'], 'cosine': round(scores[i][j], 6)} for j in neighbors])
        for j in neighbors:
            pair = tuple(sorted((i, j)))
            if pair not in pairs:
                pairs.add(pair)
                edges.append({'source': chosen[i]['id'], 'target': chosen[j]['id'], 'cosine': round(scores[i][j], 6)})
    query = None
    if question is not None:
        encoded = (neural_client.embed([semantic_policy.retrieval_query(question)])[0][0]
                   if neural else vectors(question)[0])
        query_vector = normalized(encoded, dimension)
        ranks = sorted(range(len(chosen)), key=lambda i: (-dot(query_vector, dense[i]), chosen[i]['id']))
        query = {'text': question, 'position': project(query_vector, mean, axes),
                 'matches': [{'id': chosen[i]['id'], 'cosine': round(max(-1.0, min(1.0, dot(query_vector, dense[i]))), 6)} for i in ranks],
                 'is_rag_answer': False, 'ranking': 'dense cosine only; no hybrid retrieval or answer gate'}
    # Fail closed if the head, ACL-visible corpus or expiry changed during I/O.
    if catalog.read(who) != initial:
        raise RequestError('EMBEDDING_VIEW_CORPUS_CHANGED_RELOAD', 409)
    return {'points': chosen, 'edges': edges, 'query': query, 'release_id': release,
            'embedding_version': initial['embedding_version'], 'original_dimensions': dimension,
            'projection': {'method': 'PCA', 'dimensions': 3, 'retained_variance': round(retained, 6),
                           'axis_variance': [round(x, 6) for x in ratios], 'fit': 'current displayed corpus only'},
            'total_chunks': len(canonical), 'sampled': len(chosen) < len(canonical), 'max_points': MAX_POINTS,
            'source': 'Qdrant dense vectors; PostgreSQL canonical text', 'cloud_calls': 0,
            'similarity_is_confidence': False}
