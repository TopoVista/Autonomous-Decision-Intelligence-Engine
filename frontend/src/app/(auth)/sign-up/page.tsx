import { SignUp } from "@clerk/nextjs";
import { AuthFrame } from "@/components/auth/AuthFrame";

export const dynamic = "force-dynamic";

export default function SignUpPage() {
  return (
    <AuthFrame>
      <SignUp routing="path" path="/sign-up" signInUrl="/sign-in" afterSignUpUrl="/dashboard" />
    </AuthFrame>
  );
}
