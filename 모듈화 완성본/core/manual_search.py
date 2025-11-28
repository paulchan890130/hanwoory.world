# core/manual_search.py

import requests


BACKEND_SEARCH_URL = "https://hanwoory.onrender.com/search"


def search_via_server(question: str) -> str:
    """
    Flask 중간서버(https://hanwoory.onrender.com/search)에
    {"question": "..."} 형태로 POST 해서 답변 문자열을 돌려받는다.
    """
    try:
        res = requests.post(
            BACKEND_SEARCH_URL,
            json={"question": question},
            timeout=30,
        )
        if res.status_code == 200:
            try:
                data = res.json()
            except ValueError:
                return "서버에서 JSON 형식이 아닌 응답을 받았습니다."
            return data.get("answer", "답변을 받을 수 없습니다.")
        else:
            error_detail = res.text
            try:
                error_json = res.json()
                error_detail = error_json.get("detail", res.text)
            except ValueError:
                pass
            return f"서버 오류: {res.status_code} - {error_detail}"
    except requests.exceptions.Timeout:
        return "요청 시간 초과: 서버가 응답하지 않습니다."
    except requests.exceptions.RequestException as e:
        return f"요청 실패 (네트워크 또는 서버 문제): {str(e)}"
    except Exception as e:
        return f"요청 중 알 수 없는 오류: {str(e)}"
