import { SignIn } from "@clerk/nextjs";
import { AuthFrame } from "@/components/auth/AuthFrame";

// Clerk's path-based widget needs request-time rendering; attempting to
// prerender it can fail during Next's production static-generation pass.
export const dynamic = "force-dynamic";

export default function SignInPage() {
  return (
    <AuthFrame>
      <SignIn routing="path" path="/sign-in" signUpUrl="/sign-up" afterSignInUrl="/dashboard" />
    </AuthFrame>
  );
}
