import logging
from django.http import JsonResponse, HttpResponse

logger = logging.getLogger(__name__)


class AuthErrorCatchMiddleware:
    """
    Catches ALL exceptions on /auth/ pages and returns
    a clean error page instead of Django debug error page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith('/auth/'):
            try:
                return self.get_response(request)
            except Exception as e:
                logger.error(f"Auth exception [{path}]: {e}", exc_info=True)
                return self._clean_error(request)
        return self.get_response(request)

    def _clean_error(self, request):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse(
                {'success': False, 'error': 'Something went wrong. Please try again.'},
                status=500
            )
        html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Error - Event Directory and Logistic</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
  <style>
    body {
      font-family: 'Inter', sans-serif;
      background: linear-gradient(135deg, #f8fafc 0%, #eef2ff 50%, #fff7ed 100%);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .error-card {
      background: #fff;
      border: 1px solid #e5e7eb;
      border-radius: 24px;
      padding: 3rem;
      max-width: 420px;
      text-align: center;
      box-shadow: 0 10px 40px rgba(0,0,0,0.1);
    }
    .error-icon { font-size: 3rem; color: #f5a623; margin-bottom: 1rem; }
    .error-title { font-size: 1.5rem; font-weight: 700; color: #111827; margin-bottom: 0.5rem; }
    .error-msg { color: #6b7280; font-size: 0.9rem; margin-bottom: 1.5rem; }
    .btn-gold {
      background: linear-gradient(135deg, #f5a623, #c8851b);
      color: #000; font-weight: 700; border: none;
      border-radius: 10px; padding: 0.75rem 2rem;
      text-decoration: none; display: inline-block;
    }
    .btn-gold:hover { filter: brightness(1.1); color: #000; }
  </style>
</head>
<body>
  <div class="error-card">
    <div class="error-icon">&#9888;</div>
    <div class="error-title">Something Went Wrong</div>
    <p class="error-msg">We encountered an error. Please try again.</p>
    <a href="/auth/login/" class="btn btn-gold">Back to Login</a>
  </div>
</body>
</html>"""
        return HttpResponse(html, status=500)
