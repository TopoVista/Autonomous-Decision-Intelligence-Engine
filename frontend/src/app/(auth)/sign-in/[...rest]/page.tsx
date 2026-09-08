import { SignIn } from "@clerk/nextjs";
import { AuthFrame } from "@/components/auth/AuthFrame";

export const dynamic = "force-dynamic";

export default function SignInCatchAllPage() {
  return (
    <AuthFrame>
      <SignIn routing="path" path="/sign-in" signUpUrl="/sign-up" afterSignInUrl="/dashboard" />
    </AuthFrame>
  );
}
