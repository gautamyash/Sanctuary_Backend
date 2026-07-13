from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers import (
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    UserSerializer,
    UserUpdateSerializer,
)

User = get_user_model()


class RegisterView(generics.CreateAPIView):
    """POST name/email/password → creates account and returns JWT pair."""

    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "user": UserSerializer(user).data,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=201,
        )


class MeView(generics.RetrieveUpdateAPIView):
    """GET/PATCH the authenticated user's own profile (name, phone, gender,
    date_of_birth, profile_photo). Email/id/date_joined/is_staff stay
    read-only here — unchanged for every existing consumer that only GETs
    this endpoint."""

    def get_object(self):
        return self.request.user

    def get_serializer_class(self):
        return UserSerializer if self.request.method == "GET" else UserUpdateSerializer

    def update(self, request, *args, **kwargs):
        super().update(request, *args, **kwargs)
        # Always respond with the full read shape, regardless of which
        # fields the PATCH touched.
        return Response(UserSerializer(request.user).data)


class PasswordResetRequestView(APIView):
    """POST /api/auth/password-reset/request/ {email}

    Always returns 200 with a generic message, whether or not the email
    matches an account, to avoid leaking which emails are registered. If it
    does match, emails a reset link containing uid + token (uses Django's
    EMAIL_BACKEND — console backend in dev, real SMTP once configured via
    environment variables in production)."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        user = User.objects.filter(email__iexact=email).first()
        if user is not None:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            send_mail(
                subject="Reset your Sanctuary Health password",
                message=(
                    "Use the following details in the Sanctuary Health app to "
                    f"reset your password.\n\nuid: {uid}\ntoken: {token}\n\n"
                    "If you did not request this, you can ignore this email."
                ),
                from_email=None,
                recipient_list=[user.email],
                fail_silently=True,
            )

        return Response(
            {
                "detail": "If an account exists for that email, a reset "
                "code has been sent."
            }
        )


class PasswordResetConfirmView(APIView):
    """POST /api/auth/password-reset/confirm/ {uid, token, new_password}"""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            user_id = force_str(urlsafe_base64_decode(data["uid"]))
            user = User.objects.get(pk=user_id)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            return Response({"detail": "Invalid reset link."}, status=400)

        if not default_token_generator.check_token(user, data["token"]):
            return Response({"detail": "Invalid or expired reset link."}, status=400)

        user.set_password(data["new_password"])
        user.save(update_fields=["password"])
        return Response(
            {"detail": "Password has been reset. You can now sign in."},
            status=status.HTTP_200_OK,
        )
